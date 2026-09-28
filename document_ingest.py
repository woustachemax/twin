"""Document ingestion: extract text from a user-provided PDF or receipt photo so Twin can
answer questions about it in chat, following the same fetch-then-format-into-context shape
as research_search.py's filing lookups.

Unlike a SEC filing, a receipt or personal PDF is the user's own data, not a public company's.
Its extracted content is marked (📎, see format_document_context()) so buddy.py's
scrub_system() can find it, but that marker is NOT a bypass the way FLIGHT_LINE_RE/
FILING_LINE_RE are - redact.redact()'s PII categories (email, phone, government IDs, credit
cards, addresses) still run on it unconditionally. The only thing skipped is the generic
currency/account/number redaction built for SMS bank-transaction text, so a receipt's actual
total or date survives - reading the real numbers back is the whole point of this feature.

Images (JPG/PNG) are OCR'd with Donut (naver-clova-ix/donut-base-finetuned-cord-v2, MIT
licensed), a ~800MB model fine-tuned specifically on receipts (the CORD dataset). It runs
fine on CPU - no GPU required - at the cost of a one-time download cached under
~/.cache/huggingface, and a few seconds of inference per image. Because it's a receipt-
specialized checkpoint, output quality on other kinds of document photos will be weaker;
that's a known v1 scope limitation, not a bug.

PDFs are handled by pypdfium2 (Apache-2.0/BSD-3-Clause, no system dependency like poppler):
the embedded text layer is used directly when a page has one, and any page whose text layer
comes back too short is rasterized to an image and OCR'd through the same Donut path.
"""
import hashlib
import os
import re

import pypdfium2 as pdfium
from PIL import Image

MAX_CONTEXT_CHARS = 20000  # mirrors research_search.py's MAX_EXCERPT_CHARS
MIN_PAGE_TEXT_CHARS = 20  # below this, a PDF page is treated as scanned/image-only
DONUT_MODEL_NAME = "naver-clova-ix/donut-base-finetuned-cord-v2"
RENDER_SCALE = 2.0  # ~144 DPI when rasterizing a scanned PDF page

SUPPORTED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS + (".pdf",)


class DocumentIngestError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


_donut_processor = None
_donut_model = None


def _load_donut():
    """Loads once per process; subsequent calls reuse the cached model/processor."""
    global _donut_processor, _donut_model
    if _donut_model is not None:
        return _donut_processor, _donut_model
    try:
        from transformers import DonutProcessor, VisionEncoderDecoderModel
    except ImportError:
        raise DocumentIngestError("model_unavailable", "transformers isn't installed")
    try:
        _donut_processor = DonutProcessor.from_pretrained(DONUT_MODEL_NAME)
        _donut_model = VisionEncoderDecoderModel.from_pretrained(DONUT_MODEL_NAME)
        _donut_model.eval()
    except Exception as e:
        raise DocumentIngestError("model_unavailable", str(e))
    return _donut_processor, _donut_model


def _flatten_donut_json(value, lines):
    if isinstance(value, dict):
        for key, val in value.items():
            if isinstance(val, (dict, list)):
                _flatten_donut_json(val, lines)
            else:
                lines.append(f"{key}: {val}")
    elif isinstance(value, list):
        for item in value:
            _flatten_donut_json(item, lines)
    else:
        lines.append(str(value))


def ocr_with_donut(image):
    """image: a PIL.Image. Returns readable plain text flattened from Donut's structured output."""
    import torch

    processor, model = _load_donut()
    task_prompt = "<s_cord-v2>"
    decoder_input_ids = processor.tokenizer(
        task_prompt, add_special_tokens=False, return_tensors="pt"
    ).input_ids
    pixel_values = processor(image.convert("RGB"), return_tensors="pt").pixel_values
    with torch.no_grad():
        outputs = model.generate(
            pixel_values,
            decoder_input_ids=decoder_input_ids,
            max_length=model.decoder.config.max_position_embeddings,
            pad_token_id=processor.tokenizer.pad_token_id,
            eos_token_id=processor.tokenizer.eos_token_id,
            use_cache=True,
            bad_words_ids=[[processor.tokenizer.unk_token_id]],
            return_dict_in_generate=True,
        )
    sequence = processor.batch_decode(outputs.sequences)[0]
    sequence = sequence.replace(processor.tokenizer.eos_token, "").replace(processor.tokenizer.pad_token, "")
    sequence = re.sub(r"<s_[^>]*>", "", sequence, count=1).strip()  # drop the leading task token
    parsed = processor.token2json(sequence)
    lines = []
    _flatten_donut_json(parsed, lines)
    return "\n".join(lines)


def _extract_pdf(path):
    try:
        pdf = pdfium.PdfDocument(path)
    except Exception as e:
        raise DocumentIngestError("unreadable", str(e))
    pages_text = []
    methods = set()
    try:
        for page in pdf:
            textpage = page.get_textpage()
            text = textpage.get_text_range().strip()
            textpage.close()
            if len(text) >= MIN_PAGE_TEXT_CHARS:
                pages_text.append(text)
                methods.add("pdf_text")
            else:
                bitmap = page.render(scale=RENDER_SCALE)
                image = bitmap.to_pil()
                pages_text.append(ocr_with_donut(image))
                methods.add("donut")
            page.close()
    finally:
        pdf.close()
    if methods == {"pdf_text"}:
        method = "pdf_text"
    elif methods == {"donut"}:
        method = "donut"
    else:
        method = "pdf_text+donut"
    return "\n\n".join(p for p in pages_text if p), method


def ingest_document(path):
    """Extract text from path (.pdf/.jpg/.jpeg/.png). Returns a dict ready for storage and
    for injection into a chat context, or raises DocumentIngestError."""
    if not os.path.isfile(path):
        raise DocumentIngestError("not_found", path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise DocumentIngestError("unsupported_type", ext)

    if ext == ".pdf":
        text, method = _extract_pdf(path)
        doc_type = "pdf"
    else:
        try:
            image = Image.open(path)
        except Exception as e:
            raise DocumentIngestError("unreadable", str(e))
        text = ocr_with_donut(image)
        method = "donut"
        doc_type = "image"

    text = text.strip()
    if not text:
        raise DocumentIngestError("empty", path)

    return {
        "filename": os.path.basename(path),
        "doc_type": doc_type,
        "extraction_method": method,
        "content": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "char_count": len(text),
        "context_text": text[:MAX_CONTEXT_CHARS],
    }


def format_document_context(doc):
    """Plain-text block for the model's system prompt, one line per fact, each prefixed
    with 📎. Unlike FLIGHT_LINE_RE/FILING_LINE_RE, this prefix is NOT a scrub bypass - in
    buddy.py's scrub_system(), a 📎-marked line still goes through the full scrub()/redact()
    pipeline, PII categories included. The only difference is that scrub() is called with
    skip_amounts=True for these lines, so a receipt's real total or date isn't also wiped by
    the generic currency/account/number patterns that exist for SMS bank-transaction text -
    this is the user's own document, and reading its actual numbers back is the whole point."""
    header = f"📎 {doc['filename']} ({doc['doc_type']}, extracted via {doc['extraction_method']})"
    lines = [header]
    for line in doc["context_text"].split("\n"):
        line = line.strip()
        if line:
            lines.append(f"📎 {line}")
    return "\n".join(lines)
