"""Default Windows loopback selection and PCM conversion."""
from __future__ import annotations

import numpy
import soundcard

from ..errors import CastingError
from ..recovery import RecoveryError


def default_loopback_microphone() -> soundcard.Microphone:
    try:
        speaker = soundcard.default_speaker()
        if speaker is None:
            raise RecoveryError("Windows does not have a default audio output device.", "audio")
        microphone = soundcard.get_microphone(
            id=speaker.name,
            include_loopback=True,
        )
        if not microphone.isloopback:
            raise RecoveryError(
                "Could not open the default Windows output as a loopback capture.", "audio"
            )
        return microphone
    except CastingError:
        raise
    except Exception as exc:
        raise RecoveryError(
            f"Could not access system-audio loopback for the default Windows "
            f"output: {exc}", "audio"
        ) from exc


def audio_to_pcm16(samples: numpy.ndarray) -> bytes:
    if samples.ndim != 2 or samples.shape[1] < 2:
        raise CastingError(
            "The system-audio loopback did not provide at least two audio channels."
        )
    stereo = numpy.clip(samples[:, :2], -1.0, 1.0)
    return (stereo * 32767).astype("<i2").tobytes()
