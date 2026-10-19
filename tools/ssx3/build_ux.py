#!/usr/bin/env python3
"""First-build experience: step banners, host-scaled estimates, final summary.

build-ux-1. Small output/UX layer for the one-command builds
(`./build android --ssx3-iso ...` / `./build ios --ssx3-iso ...`).
No build logic lives here: build.py calls into these printers, and the
Tricky driver (tools/ssx3/tricky/build_tricky.py) uses TrickyProgress
for its per-course/phase lines. Everything is plain `\n` lines; ANSI
is only emitted when use_color() allows it (NO_COLOR / non-TTY safe).

Measured anchors (ranges, never promises):
- prepare 41+4 s Linux (astra-buildtime-1) -> 1-2 min here.
- android-libs 276 s Linux (astra-buildtime-1) -> 3-6 min.
- Gradle android 578/673 s Linux, cached 6m23s (release-pb71-1) -> 8-16 min.
- ios-libs has no isolated fresh wall (astra-buildtime-2) -> 3-6 min.
- Xcode ios: whole fresh stock iOS ~17 min (release-pb71-1) -> 10-20 min.
- Tricky worlds: r13 nine-course work sum 299 min serial, ~104 min at
  3 workers (tk-speed-1); scaled by this host's course-worker sizing.
- Tricky tail (speech/music/gems/layout): 15-35 min (astra-buildtime-2;
  anchors: r13b 20.1 min to the gems failure, K 21.3 min partial).
- install 1-3 min (transfer; unmeasured, labeled an estimate).
"""
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ISSUE_URL = ('https://github.com/brad-richardson/ssx3-120/issues/new'
             '?template=build-failure.md')

# Tricky sizing mirror (tricky-manifest-1 decision): courses =
# max(1, min(floor(cores/3), RAM cap)); kept as a 6-line mirror (not an
# import) so the driver and the tests never depend on each other.
_COURSE_PEAK_GB = 4.0
_RAM_RESERVE_GB = 8.0
_SERIAL_WORK_MIN = 299.0  # tk-speed-1 r13 nine-course work sum, minutes.
_HOST_FACTOR = (0.75, 1.50)  # astra-buildtime-2 H interval (assumption).

# (step, lo_min, hi_min, source tag) for the non-Tricky steps.
_BASE_ESTIMATES = {
    'doctor': (0, 1, 'tool probes only'),
    'prepare': (1, 2, 'astra-buildtime-1 41+4s Linux'),
    'android-libs': (3, 6, 'astra-buildtime-1 276s Linux'),
    'android': (8, 16, 'astra-buildtime-1 578/673s; release-pb71-1 cached 6m23s'),
    'ios-libs': (3, 6, 'astra-buildtime-2 (no isolated fresh wall)'),
    'ios': (10, 20, 'release-pb71-1 whole fresh stock iOS ~17 min'),
    'install': (1, 3, 'transfer; unmeasured'),
}
_TAIL_ESTIMATE = (15, 35, 'astra-buildtime-2; r13b 20.1 min, K 21.3 min')

# Exact per-step resume contract (verified against build.py predicates).
RESUME_NOTES = {
    'doctor': 'doctor checks only and changes nothing; re-run is cheap',
    'prepare': 'prepare skips when inputs.json is current for this disc, '
               'otherwise it regenerates (stale outputs are dropped, the '
               'finished codegen publishes atomically)',
    'tricky': 'tricky skips when tricky_inputs.json and every payload SHA '
              'are current; a failed run leaves <work>/tricky/ in place and '
              'the step refuses until it is removed, so a failed Tricky run '
              'does NOT resume mid-step',
    'android-libs': 'android-libs skips when the staged libraries and the '
                    'recorded dependency/vendor pins are current, otherwise '
                    'it rebuilds',
    'ios-libs': 'ios-libs skips when the staged archives and receipt match, '
                'otherwise it rebuilds',
    'android': 'android always runs; Gradle is incremental',
    'ios': 'ios always runs; Xcode is incremental',
    'install': 'install is re-runnable; device files skip on SHA256 match '
               'and app data is preserved (adb install -r)',
}


def use_color(stream=None):
    """Whether ANSI styling is allowed on this stream."""
    if os.environ.get('NO_COLOR') is not None:
        return False
    target = stream if stream is not None else sys.stdout
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):
        return False


def style(text, code, enabled):
    """Wrap text in one ANSI code only when enabled."""
    if not enabled:
        return text
    return '\033[' + code + 'm' + text + '\033[0m'


def fmt_elapsed(seconds):
    """M:SS or H:MM:SS for a wall duration."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return '%d:%02d:%02d' % (hours, minutes, secs)
    return '%d:%02d' % (minutes, secs)


def fmt_range(lo, hi):
    """'X-Y min' estimate range (whole minutes, lo <= hi)."""
    lo, hi = int(lo), int(hi)
    if lo > hi:
        lo, hi = hi, lo
    if lo == hi:
        return '%d min' % lo
    return '%d-%d min' % (lo, hi)


def host_facts():
    """(cores, ram_gb or None) for estimate scaling (stdlib only)."""
    cores = os.cpu_count() or 0
    ram_gb = None
    try:
        if sys.platform == 'darwin':
            import subprocess
            out = subprocess.check_output(['sysctl', '-n', 'hw.memsize'],
                                          text=True, timeout=10)
            ram_gb = int(out.strip()) / 1024**3
        elif sys.platform.startswith('linux'):
            with open('/proc/meminfo') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        ram_gb = int(line.split()[1]) / 1024**2
                        break
    except (OSError, ValueError, subprocess.CalledProcessError):
        ram_gb = None
    return cores, ram_gb


def course_workers(cores, ram_gb, jobs=8, nready=9):
    """Mirror of the tricky-manifest-1 course sizing for estimates.

    courses = max(1, min(nready, floor(cores/3), RAM cap)) where the RAM
    cap = floor((ram - reserve) / per-course peak); unknown RAM skips
    the RAM cap. Returns (workers, note).
    """
    cores = max(1, int(cores or 1))
    workers = max(1, min(nready, cores // 3))
    if ram_gb:
        ram_cap = max(1, int((ram_gb - _RAM_RESERVE_GB) // _COURSE_PEAK_GB))
        workers = max(1, min(workers, ram_cap))
    if jobs:
        workers = max(1, min(workers, int(jobs)))
    note = 'floor(cores/3) with measured-peak RAM cap'
    return workers, note


def estimate_steps(*, target, tricky, audio_on=False, cores=None, ram_gb=None,
                   jobs=8, install=False):
    """Ordered [(step, lo, hi)] estimates for a one-command build.

    Always ranges, always labeled estimates by the caller. Never blocks.
    """
    if cores is None:
        cores, _mem = host_facts()
        if ram_gb is None:
            ram_gb = _mem
    libs = 'android-libs' if target == 'android' else 'ios-libs'
    build = 'android' if target == 'android' else 'ios'
    steps = ['doctor', 'prepare']
    if tricky:
        steps.append('tricky')
    steps += [libs, build]
    if install:
        steps.append('install')
    out = []
    for name in steps:
        if name == 'tricky':
            workers, _note = course_workers(cores, ram_gb, jobs)
            lo = _SERIAL_WORK_MIN / workers * _HOST_FACTOR[0] + _TAIL_ESTIMATE[0]
            hi = _SERIAL_WORK_MIN / workers * _HOST_FACTOR[1] + _TAIL_ESTIMATE[1]
            out.append((name, int(round(lo / 5.0)) * 5,
                        int(round(hi / 5.0)) * 5))
        else:
            lo, hi, _src = _BASE_ESTIMATES[name]
            out.append((name, lo, hi))
    return out


def estimate_table_text(*, target, tricky, audio_on=False, cores=None,
                        ram_gb=None, jobs=8, install=False):
    """Small measured-cost table, host-scaled, labeled an estimate."""
    if cores is None:
        cores, _mem = host_facts()
        if ram_gb is None:
            ram_gb = _mem
    lines = ['estimates for this machine (cores=%s, RAM=%s; ranges, not promises):'
             % (cores or '?', ('%.0f GB' % ram_gb) if ram_gb else '?')]
    planned = estimate_steps(target=target, tricky=tricky, audio_on=audio_on,
                             cores=cores, ram_gb=ram_gb, jobs=jobs,
                             install=install)
    for name, lo, hi in planned:
        if name == 'tricky':
            workers, _note = course_workers(cores, ram_gb, jobs)
            src = ('tk-speed-1 r13 serial 299 min / %d workers x H0.75-1.5 '
                   '+ tail 15-35 min' % workers)
            if not audio_on:
                src += '; audio off shortens the tail'
        else:
            src = _BASE_ESTIMATES[name][2]
        lines.append('  %-12s %8s  (%s)' % (name, fmt_range(lo, hi), src))
    total_lo = sum(lo for _, lo, _hi in planned)
    total_hi = sum(hi for _, _lo, hi in planned)
    lines.append('  %-12s %8s  (estimate total)' % ('total', fmt_range(total_lo, total_hi)))
    return '\n'.join(lines)


def step_banner(k, n, name, lo, hi, *, color=False):
    """'[step k/N] name (est. X-Y min on this machine)'."""
    return '[step %d/%d] %s (est. %s on this machine)' % (
        k, n, name, fmt_range(lo, hi))


def step_done(name, seconds, *, color=False):
    """Elapsed-time completion line."""
    line = '%s done in %s' % (name, fmt_elapsed(seconds))
    return style(line, '32', color)


def failure_lines(step, log_path, resume_note, issue_url=ISSUE_URL,
                  bundle_path=None):
    """Failure help: step + log, resume truth, issue URL, support bundle."""
    lines = ['BUILD FAILED at %s; log: %s' % (step, log_path),
             're-run the same command to resume; %s.' % resume_note,
             'open an issue with the build-failure template: %s' % issue_url]
    if bundle_path is not None:
        lines.append('support bundle (personal paths and device serials '
                     'redacted): %s' % bundle_path)
    else:
        lines.append('no support bundle was written (collect one with '
                     'build_ux.collect_support_bundle before reporting)')
    return lines


def redact_text(text, home=None):
    """Strip personal paths and device serials from failure evidence."""
    if home is None:
        try:
            home = str(Path.home())
        except (OSError, RuntimeError):
            home = None
    if home:
        text = text.replace(home, '~')
    # Other macOS home dirs that are not this user's.
    text = re.sub(r'/Users/[^\s:/\'"]+', '~', text)
    # adb device selectors and bare serial-looking tokens after -s.
    text = re.sub(r'(-s\s+)\S+', r'\1<serial>', text)
    text = re.sub(r'\b(serial\s*[:=]\s*)\S+', r'\1<serial>', text,
                  flags=re.IGNORECASE)
    # iOS device UDIDs (8-4-4-4-12 and the Apple 8-16 devicectl form).
    text = re.sub(r'\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-'
                  r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b', '<udid>', text)
    text = re.sub(r'\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}\b', '<udid>', text)
    return text


def append_log(work, line):
    """Append one line to the one-command log; never raises."""
    try:
        with open(Path(work) / 'build-ux.log', 'a') as f:
            f.write(line + '\n')
    except OSError:
        pass


def collect_support_bundle(work, *, builder_version='unknown', host=None,
                           steps=(), log_tail_lines=40):
    """Write a redacted failure bundle beside the work dir; return its path.

    Gathers only what a builder failure needs: host facts, builder
    version, per-step outcomes, receipt identities (SHAs, never disc
    bytes), and the tail of build-ux.log. Personal paths and device
    serials are redacted with redact_text. Never raises: returns None
    when nothing could be written.
    """
    work = Path(work)
    try:
        cores, ram_gb = host_facts() if host is None else host
        lines = ['ssx3-120 build support bundle',
                 'created_utc: ' + datetime.now(timezone.utc).strftime(
                     '%Y-%m-%dT%H:%M:%SZ'),
                 'builder: %s' % builder_version,
                 'host: %s/%s cores=%s ram_gb=%s' % (
                     sys.platform, os.uname().machine if hasattr(os, 'uname')
                     else '?', cores, ('%.1f' % ram_gb) if ram_gb else '?'),
                 'work: <workdir>',
                 'steps:']
        for name, outcome in steps:
            lines.append('  %s: %s' % (name, outcome))
        lines.append('receipts:')
        for receipt in ('inputs.json', 'tricky_inputs.json',
                        'android-app.json', 'ios-app.json'):
            candidate = work / receipt
            if not candidate.is_file():
                lines.append('  %s: absent' % receipt)
                continue
            try:
                record = candidate.read_text()
            except OSError:
                lines.append('  %s: unreadable' % receipt)
                continue
            # Keep identities (SHAs, pins, versions); drop local paths.
            try:
                import json as _json
                data = _json.loads(record)
                keep = {k: v for k, v in data.items()
                        if k != 'iso' and k != 'ssx3_iso' and k != 'tricky_iso'
                        and not k.endswith('_path')}
                for key in ('iso', 'ssx3_iso', 'tricky_iso'):
                    if isinstance(data.get(key), str):
                        keep[key + '_basename'] = Path(data[key]).name
                record = _json.dumps(keep, indent=2, sort_keys=True)
            except ValueError:
                pass
            lines.append('  %s:\n%s' % (receipt, record))
        lines.append('build-ux.log tail:')
        try:
            tail = (work / 'build-ux.log').read_text().splitlines()
            lines.extend('  ' + line for line in tail[-log_tail_lines:])
        except OSError:
            lines.append('  (no build-ux.log)')
        text = redact_text('\n'.join(lines) + '\n')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        dest = work / ('support-bundle-%s.txt' % stamp)
        dest.write_text(text)
        return dest
    except OSError:
        return None


class TrickyProgress:
    """Modest-rate Tricky progress: per-course/phase lines + 60 s heartbeat.

    Prints plain lines only (no carriage-return spinners), so CI logs stay
    readable. A heartbeat line is emitted when a minute passes with no
    other change.
    """

    def __init__(self, total, *, stream=None, heartbeat_s=60, time_fn=None):
        self.total = total
        self.stream = stream if stream is not None else sys.stdout
        self.heartbeat_s = heartbeat_s
        self._now = time_fn if time_fn is not None else time.time
        self._t0 = self._now()
        self._last = self._t0
        self.finished = []
        self.running = []

    def _emit(self, detail):
        line = ('tricky: worlds %d/%d%s · elapsed %s'
                % (len(self.finished), self.total, detail,
                   fmt_elapsed(self._now() - self._t0)))
        print(line, file=self.stream, flush=True)
        self._last = self._now()

    def course_started(self, name):
        if name not in self.running:
            self.running.append(name)

    def course_finished(self, name):
        if name in self.running:
            self.running.remove(name)
        if name not in self.finished:
            self.finished.append(name)
        detail = ' (%s done%s)' % (
            ', '.join(sorted(self.finished)),
            '; %s running' % ', '.join(sorted(self.running))
            if self.running else '')
        self._emit(detail)

    def phase(self, name, seconds=None):
        if seconds is None:
            print('tricky: phase %s done' % name, file=self.stream,
                  flush=True)
        else:
            print('tricky: phase %s done in %s' % (name, fmt_elapsed(seconds)),
                  file=self.stream, flush=True)
        self._last = self._now()

    def maybe_heartbeat(self, force=False):
        if force or self._now() - self._last >= self.heartbeat_s:
            detail = ''
            if self.finished or self.running:
                detail = ' (%s done%s)' % (
                    ', '.join(sorted(self.finished)) or 'none yet',
                    '; %s running' % ', '.join(sorted(self.running))
                    if self.running else '')
            self._emit(detail)


def run_step(work, k, n, name, lo, hi, func, *, color=False):
    """Print the banner, run func(), print elapsed; failure help on error.

    Appends start/finish/failure lines to <work>/build-ux.log and, on
    error, prints failure_lines (step + log path, exact resume note,
    issue URL, redacted support bundle) before re-raising.
    """
    print(step_banner(k, n, name, lo, hi, color=color), flush=True)
    append_log(work, '[step %d/%d] %s started' % (k, n, name))
    t0 = time.time()
    try:
        result = func()
    except Exception:
        seconds = time.time() - t0
        append_log(work, '[step %d/%d] %s FAILED after %s'
                   % (k, n, name, fmt_elapsed(seconds)))
        try:
            bundle = collect_support_bundle(work, steps=[(name, 'FAILED')])
        except Exception:
            bundle = None
        for line in failure_lines(name, Path(work) / 'build-ux.log',
                                  RESUME_NOTES.get(name, 're-run to retry'),
                                  bundle_path=bundle):
            print(line, flush=True)
        raise
    seconds = time.time() - t0
    print(step_done(name, seconds, color=color), flush=True)
    append_log(work, '[step %d/%d] %s done in %s'
               % (k, n, name, fmt_elapsed(seconds)))
    return result


def print_final_summary(*, total_seconds, artifact, install_cmd=None,
                        installed=None, tricky=False, audio=None,
                        audio_why='', skips=(), color=False, signing=None):
    """Final summary: time, artifact, install, keystore, Tricky, rerun line.

    signing overrides the Android keystore reminder (iOS passes its
    signing-identity line instead).
    """
    print(style('Build finished in %s.' % fmt_elapsed(total_seconds),
                '1', color), flush=True)
    print('Artifact: %s' % artifact, flush=True)
    if installed:
        print('Installed: %s' % installed, flush=True)
    elif install_cmd:
        print('Next: %s' % install_cmd, flush=True)
    if signing is None:
        signing = ('Back up your keystore; a different key forces an '
                   'uninstall, which wipes saves.')
    print(signing, flush=True)
    if tricky:
        print('Tricky: yes (audio %s%s).'
              % (audio or 'unknown',
                 ' — ' + audio_why if audio_why else ''), flush=True)
    else:
        print('Tricky: no (stock SSX 3 only; pass --tricky-iso to build '
              'it).', flush=True)
    if skips:
        print('Rerun: ' + '; '.join(skips) + '.', flush=True)
