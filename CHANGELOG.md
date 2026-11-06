# Changelog

All notable changes to SIXAMP are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-07

First public release.

### Added
- Main window drawn entirely in Sixel graphics: seven-segment time display (elapsed or
  remaining), scrolling title, bitrate and sample rate, mono/stereo lights, volume and
  balance sliders, seek bar, transport buttons, shuffle and repeat.
- Visualizer with spectrum analyser (with falling peaks) and oscilloscope modes.
- Ten-band linear-phase graphic equalizer with preamp, response curve and 11 presets.
- Playlist window with ID3v1/v2 tags, durations, total time, scrollbar, add, remove,
  clear and sort, and six entry formats (`f` or the FMT button, or `--format`).
- File browser, jump-to-track search, track info and help windows.
- Plays MP3, FLAC, WAV and Ogg Vorbis; opens files, folders and `.m3u` playlists.
- Full keyboard control with the classic `z x c v b` keys, and full mouse control
  including slider dragging and the wheel.
- Automatic layout (stacked or side by side) and pixel scale, `d` to change the scale.
- Settings, playlist and equalizer are remembered between runs.
- Runs on Linux, macOS and Windows (Windows Terminal 1.22 or newer).
- Only repaints what changed, and leaves the area around the windows unpainted so
  terminal backgrounds show through.
- `--probe` to check a terminal's Sixel support, `--debug` for a diagnostic log,
  `--cell` for terminals that cannot report their cell size.
- Exact mouse positions and live cell sizes in [pyterm](https://github.com/pez2001/pyterm),
  through its `sixamp_support` plugin.

[1.0.0]: https://github.com/pez2001/sixamp/releases/tag/v1.0.0
