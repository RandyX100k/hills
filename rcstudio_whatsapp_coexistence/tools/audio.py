"""Voice notes.

WhatsApp plays an audio message as a voice note only when it is OGG with mono Opus audio.
The normal path makes that in the browser: Firefox records OGG/Opus, Chrome and Edge record
WebM/Opus and the inbox re-packages it (static/src/inbox/webm_to_ogg.js). Whatever else
arrives here (Safari's MP4/AAC, an unusual WebM) is converted by ffmpeg when the server has
it, and refused with an explanation when it does not.
"""
import logging
import os
import shutil
import subprocess
import tempfile

_logger = logging.getLogger(__name__)

SUFFIXES = {'webm': '.webm', 'ogg': '.ogg', 'mp4': '.mp4', 'x-m4a': '.m4a', 'aac': '.aac',
            'mpeg': '.mp3', 'wav': '.wav', 'x-wav': '.wav'}


class AudioError(Exception):
    """The recording could not be turned into a voice note; the message says why."""


def ffmpeg_path():
    return shutil.which('ffmpeg')


def is_voice_ready(mimetype):
    """Already what WhatsApp wants: OGG with the Opus codec."""
    mimetype = (mimetype or '').lower()
    return mimetype.startswith('audio/ogg') and 'opus' in mimetype


def _suffix(mimetype):
    subtype = (mimetype or '').split('/')[-1].split(';')[0].strip().lower()
    return SUFFIXES.get(subtype, '')


def to_voice_note(content, mimetype=''):
    """Return (bytes, 'audio/ogg'): the recording itself when it already is OGG/Opus, ffmpeg's
    conversion otherwise; AudioError when neither is possible."""
    if not content:
        raise AudioError('The recording is empty.')
    if is_voice_ready(mimetype):
        if not content.startswith(b'OggS'):
            raise AudioError('The recording says it is OGG/Opus but is not an OGG file.')
        return content, 'audio/ogg'
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise AudioError(
            'The recording is %s, which WhatsApp does not play as a voice note, and ffmpeg is '
            'not installed on the Odoo server to convert it. Record from Chrome, Edge or '
            'Firefox, which produce Opus, or install ffmpeg on the server ("sudo apt install '
            'ffmpeg") and restart Odoo; Safari needs it.' % (mimetype or 'an unknown format'))
    with tempfile.TemporaryDirectory(prefix='rcs_wa_voice_') as tmp:
        src = os.path.join(tmp, 'in' + _suffix(mimetype))
        dst = os.path.join(tmp, 'out.ogg')
        with open(src, 'wb') as fh:
            fh.write(content)
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', src,
                   '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'libopus', '-b:a', '32k',
                   '-application', 'voip', '-f', 'ogg', dst]
        try:
            proc = subprocess.run(command, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AudioError('ffmpeg could not run: %s' % exc) from exc
        if proc.returncode != 0:
            detail = proc.stderr.decode(errors='replace').strip()[-300:]
            raise AudioError('ffmpeg could not convert the recording: %s' % detail)
        with open(dst, 'rb') as fh:
            return fh.read(), 'audio/ogg'
