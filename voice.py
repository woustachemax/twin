import os
import re
import subprocess
import tempfile
import threading
import time

PERMISSION_TIMEOUT_SECONDS = 60
TRANSCRIBE_TIMEOUT_SECONDS = 20
CALIBRATION_SECONDS = 0.4
CALIBRATION_POLL_SECONDS = 0.05
SILENCE_OFFSET_DB = 10.0
SPEECH_OFFSET_DB = 15.0
SILENCE_GUARD_DB = 4.0
MIN_THRESHOLD_GAP_DB = 3.0
SMOOTHING_ALPHA = 0.4
MIN_NOISE_FLOOR_DB = -80.0
MAX_NOISE_FLOOR_DB = -10.0
FALLBACK_NOISE_FLOOR_DB = -50.0
SILENCE_HANG_SECONDS = 1.1
MIN_SPEECH_SECONDS = 0.35
MAX_COMMAND_SECONDS = 15.0
METER_POLL_SECONDS = 0.1
WAKE_SESSION_SECONDS = 45.0

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
NOTHING_HEARD_MESSAGE = "I didn't catch anything that time. Try again?"
RECORD_FAILED_MESSAGE = "I couldn't start recording just now. Try again?"
TRANSCRIBE_TIMEOUT_MESSAGE = "That took too long to make out. Try a shorter question?"
ENGINE_FAILED_MESSAGE = "I couldn't listen for your voice just now. Try again?"

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


def has_access():
    """Non-blocking, never prompts. Merely calling AVAudioEngine/SFSpeechRecognizer APIs while
    permission is undetermined can make macOS show its system prompt and block the calling
    thread until someone answers it, so anything running on the main thread (WakeWordListener)
    must check this first and back off rather than ever touching those APIs directly."""
    try:
        Speech, AVFoundation = load_frameworks()
    except VoiceInputError:
        return False
    mic_status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
    return mic_status == 3 and Speech.SFSpeechRecognizer.authorizationStatus() == 3


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
        recorder.setMeteringEnabled_(True)
        self.recorder, self.path = recorder, path

    def stop(self):
        path = self.path
        if self.recorder is not None:
            self.recorder.stop()
        self.recorder, self.path = None, None
        return path

    def wait_for_silence(self, stop_event, max_seconds=MAX_COMMAND_SECONDS):
        silence_threshold, speech_threshold = self._calibrate(stop_event)
        if silence_threshold is None:
            return
        started = time.monotonic()
        heard_speech = False
        quiet_since = None
        smoothed = None
        while time.monotonic() - started < max_seconds:
            if stop_event is not None and stop_event.is_set():
                return
            self.recorder.updateMeters()
            level = self.recorder.averagePowerForChannel_(0)
            smoothed = level if smoothed is None else smoothed + (level - smoothed) * SMOOTHING_ALPHA
            elapsed = time.monotonic() - started
            if smoothed >= speech_threshold:
                heard_speech = True
                quiet_since = None
            elif smoothed < silence_threshold:
                if quiet_since is None:
                    quiet_since = time.monotonic()
                elif (
                    heard_speech
                    and elapsed >= MIN_SPEECH_SECONDS
                    and time.monotonic() - quiet_since >= SILENCE_HANG_SECONDS
                ):
                    return
            else:
                quiet_since = None
            time.sleep(METER_POLL_SECONDS)

    def _calibrate(self, stop_event):
        started = time.monotonic()
        samples = []
        while time.monotonic() - started < CALIBRATION_SECONDS:
            if stop_event is not None and stop_event.is_set():
                return None, None
            self.recorder.updateMeters()
            level = self.recorder.averagePowerForChannel_(0)
            if level > -100:
                samples.append(level)
            time.sleep(CALIBRATION_POLL_SECONDS)
        if samples:
            samples.sort()
            floor = samples[len(samples) // 2]
            peak = samples[-1]
        else:
            floor = peak = FALLBACK_NOISE_FLOOR_DB
        floor = max(MIN_NOISE_FLOOR_DB, min(MAX_NOISE_FLOOR_DB, floor))
        peak = max(MIN_NOISE_FLOOR_DB, min(MAX_NOISE_FLOOR_DB, peak))
        silence_threshold = max(floor + SILENCE_OFFSET_DB, peak + SILENCE_GUARD_DB)
        speech_threshold = max(floor + SPEECH_OFFSET_DB, silence_threshold + MIN_THRESHOLD_GAP_DB)
        return silence_threshold, speech_threshold


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


def wake_pattern(name):
    return re.compile(r"\b(?:hi|hey|ok)\s+" + re.escape(name.lower()) + r"\b", re.I)


class WakeWordListener:
    """Listens continuously for "hi/hey <persona name>" using a live, on-device, buffer-fed
    recognizer. It only ever reports that the phrase was heard; nothing recognized, matched or
    otherwise, is handed to the caller or kept beyond that. Once heard, the caller records and
    transcribes the actual command as its own separate, one-shot capture.

    The Speech framework only delivers its result callbacks to a thread whose run loop is
    actively being pumped in the default mode. Tk's own main loop already does this
    continuously on the main thread, so this class does no threading of its own: call
    start()/stop()/pause()/resume() and, roughly every second, tick() from the same main-thread
    loop that already drives the rest of the UI (e.g. Buddy.poll()).
    """

    RETRY_COOLDOWN_SECONDS = 5.0

    def __init__(self, get_pattern, on_wake, on_error=None):
        self.get_pattern = get_pattern
        self.on_wake = on_wake
        self.on_error = on_error
        self.active = False
        self.paused = False
        self._engine = None
        self._input_node = None
        self._request = None
        self._task = None
        self._restart_at = 0.0
        self._retry_at = 0.0
        self._woken = False
        self._frameworks = None

    def start(self):
        self.active = True

    def stop(self):
        self.active = False
        self._teardown()

    def pause(self):
        self.paused = True
        self._teardown()

    def resume(self):
        self._woken = False
        self.paused = False
        self._retry_at = 0.0

    def tick(self):
        if self._task is not None and self._engine is None:
            self._cancel_task()
        if not self.active or self.paused or self._woken:
            return
        now = time.monotonic()
        if self._engine is None:
            if now < self._retry_at:
                return
            if not has_access():
                self._retry_at = now + self.RETRY_COOLDOWN_SECONDS
                return
            try:
                if self._frameworks is None:
                    self._frameworks = load_frameworks()
                self._start_session(*self._frameworks)
                self._restart_at = now + WAKE_SESSION_SECONDS
            except VoiceInputError as e:
                self._retry_at = now + self.RETRY_COOLDOWN_SECONDS
                if self.on_error:
                    self.on_error(str(e))
            return
        if now >= self._restart_at:
            self._teardown()

    def _start_session(self, Speech, AVFoundation):
        recognizer = Speech.SFSpeechRecognizer.alloc().init()
        if recognizer is None or not recognizer.isAvailable():
            raise VoiceInputError(NO_RECOGNIZER_MESSAGE)
        request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
        if recognizer.supportsOnDeviceRecognition():
            request.setRequiresOnDeviceRecognition_(True)
        request.setShouldReportPartialResults_(True)

        engine = AVFoundation.AVAudioEngine.alloc().init()
        input_node = engine.inputNode()
        audio_format = input_node.outputFormatForBus_(0)

        def tap(buffer, when):
            request.appendAudioPCMBuffer_(buffer)

        input_node.installTapOnBus_bufferSize_format_block_(0, 1024, audio_format, tap)
        engine.prepare()
        ok, error = engine.startAndReturnError_(None)
        if not ok:
            input_node.removeTapOnBus_(0)
            raise VoiceInputError(ENGINE_FAILED_MESSAGE)

        def handler(result, error):
            if self._woken:
                return
            if result is not None and self.get_pattern().search(result.bestTranscription().formattedString()):
                self._woken = True
                self._release_audio()
                self.on_wake()

        task = recognizer.recognitionTaskWithRequest_resultHandler_(request, handler)
        self._engine, self._input_node, self._request, self._task = engine, input_node, request, task

    def _release_audio(self):
        if self._input_node is not None:
            try:
                self._input_node.removeTapOnBus_(0)
            except Exception:
                pass
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass
        if self._request is not None:
            try:
                self._request.endAudio()
            except Exception:
                pass
        self._engine = self._input_node = self._request = None

    def _cancel_task(self):
        if self._task is not None:
            try:
                self._task.cancel()
            except Exception:
                pass
            self._task = None

    def _teardown(self):
        self._release_audio()
        self._cancel_task()


def voice_for(persona_key):
    return PERSONA_VOICES.get(persona_key, DEFAULT_VOICE)


def speak(text, persona_key):
    if not text.strip():
        return None
    voice, rate = voice_for(persona_key)
    try:
        return subprocess.Popen(
            ["say", "-v", voice, "-r", str(rate), text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        raise VoiceOutputError(f"couldn't speak the reply: {e!r}")


def stop_speaking():
    subprocess.run(["killall", "say"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
