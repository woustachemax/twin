import os
import subprocess
import tempfile
import threading
import time

PERMISSION_TIMEOUT_SECONDS = 60
TRANSCRIBE_TIMEOUT_SECONDS = 20

MIC_PERMISSION_MESSAGE = (
    "Microphone access isn't allowed yet. Allow it when macOS asks "
    "(or in System Settings → Privacy & Security → Microphone)."
)
SPEECH_PERMISSION_MESSAGE = (
    "Speech Recognition access isn't allowed yet. Allow it when macOS asks "
    "(or in System Settings → Privacy & Security → Speech Recognition)."
)
TIMEOUT_MESSAGE = "macOS is still waiting for you to allow microphone or speech access. Allow it when it asks, then try again."
UNAVAILABLE_MESSAGE = (
    "Voice input needs a couple more packages: "
    "pip install pyobjc-framework-Speech pyobjc-framework-AVFoundation"
)
NO_RECOGNIZER_MESSAGE = "Speech recognition isn't available for this language on this Mac right now."
NOTHING_HEARD_MESSAGE = "I didn't catch anything that time. Try holding the key down while you talk?"
RECORD_FAILED_MESSAGE = "I couldn't start recording just now. Try again?"
TRANSCRIBE_TIMEOUT_MESSAGE = "That took too long to make out. Try a shorter question?"

PERSONA_VOICES = {
    "twin": ("Samantha", 195),
    "gengar": ("Fred", 172),
    "ember": ("Samantha", 218),
    "calm": ("Moira", 162),
    "plain": ("Daniel", 186),
}
DEFAULT_VOICE = ("Samantha", 190)


class VoiceInputError(Exception):
    pass


class VoiceOutputError(Exception):
    pass


def load_frameworks():
    try:
        import AVFoundation
        import Speech
    except ImportError:
        raise VoiceInputError(UNAVAILABLE_MESSAGE)
    return Speech, AVFoundation


def request_microphone_access(AVFoundation):
    granted = {}
    done = threading.Event()

    def completion(ok):
        granted["ok"] = bool(ok)
        done.set()

    AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVFoundation.AVMediaTypeAudio, completion,
    )
    if not done.wait(PERMISSION_TIMEOUT_SECONDS):
        raise VoiceInputError(TIMEOUT_MESSAGE)
    return granted.get("ok", False)


def request_speech_access(Speech):
    granted = {}
    done = threading.Event()

    def completion(status):
        granted["status"] = status
        done.set()

    Speech.SFSpeechRecognizer.requestAuthorization_(completion)
    if not done.wait(PERMISSION_TIMEOUT_SECONDS):
        raise VoiceInputError(TIMEOUT_MESSAGE)
    return granted.get("status")


def ensure_access(allow_prompt=False):
    Speech, AVFoundation = load_frameworks()
    mic_status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
    if mic_status == 0 and allow_prompt:
        request_microphone_access(AVFoundation)
        mic_status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
    if mic_status != 3:
        raise VoiceInputError(MIC_PERMISSION_MESSAGE)
    speech_status = Speech.SFSpeechRecognizer.authorizationStatus()
    if speech_status == 0 and allow_prompt:
        speech_status = request_speech_access(Speech)
    if speech_status != 3:
        raise VoiceInputError(SPEECH_PERMISSION_MESSAGE)


class Recorder:
    def __init__(self):
        self.recorder = None
        self.path = None

    def start(self):
        _, AVFoundation = load_frameworks()
        from Foundation import NSURL

        fd, path = tempfile.mkstemp(suffix=".wav", prefix="twin_voice_")
        os.close(fd)
        settings = {
            AVFoundation.AVFormatIDKey: 1819304813,
            AVFoundation.AVSampleRateKey: 16000.0,
            AVFoundation.AVNumberOfChannelsKey: 1,
            AVFoundation.AVLinearPCMBitDepthKey: 16,
            AVFoundation.AVLinearPCMIsBigEndianKey: False,
            AVFoundation.AVLinearPCMIsFloatKey: False,
        }
        recorder, error = AVFoundation.AVAudioRecorder.alloc().initWithURL_settings_error_(
            NSURL.fileURLWithPath_(path), settings, None,
        )
        if recorder is None or not recorder.prepareToRecord() or not recorder.record():
            os.remove(path) if os.path.exists(path) else None
            raise VoiceInputError(RECORD_FAILED_MESSAGE)
        self.recorder, self.path = recorder, path

    def stop(self):
        path = self.path
        if self.recorder is not None:
            self.recorder.stop()
        self.recorder, self.path = None, None
        return path


def transcribe_file(path, timeout=TRANSCRIBE_TIMEOUT_SECONDS):
    Speech, _ = load_frameworks()
    from Foundation import NSDate, NSRunLoop, NSURL

    try:
        recognizer = Speech.SFSpeechRecognizer.alloc().init()
        if recognizer is None or not recognizer.isAvailable():
            raise VoiceInputError(NO_RECOGNIZER_MESSAGE)
        request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
        if recognizer.supportsOnDeviceRecognition():
            request.setRequiresOnDeviceRecognition_(True)
        request.setShouldReportPartialResults_(False)
        box = {}

        def handler(result, error):
            if error is not None:
                box["error"] = str(error)
            elif result is not None and result.isFinal():
                box["text"] = result.bestTranscription().formattedString()

        recognizer.recognitionTaskWithRequest_resultHandler_(request, handler)
        run_loop = NSRunLoop.currentRunLoop()
        deadline = time.monotonic() + timeout
        while "text" not in box and "error" not in box and time.monotonic() < deadline:
            run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
        if "error" in box:
            raise VoiceInputError(NOTHING_HEARD_MESSAGE)
        if "text" not in box:
            raise VoiceInputError(TRANSCRIBE_TIMEOUT_MESSAGE)
        return box["text"].strip()
    finally:
        if os.path.exists(path):
            os.remove(path)


def voice_for(persona_key):
    return PERSONA_VOICES.get(persona_key, DEFAULT_VOICE)


def speak(text, persona_key):
    if not text.strip():
        return
    voice, rate = voice_for(persona_key)
    try:
        subprocess.Popen(
            ["say", "-v", voice, "-r", str(rate), text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        raise VoiceOutputError(f"couldn't speak the reply: {e!r}")


def stop_speaking():
    subprocess.run(["killall", "say"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
