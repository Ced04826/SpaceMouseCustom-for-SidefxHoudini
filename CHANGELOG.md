# Changelog

## [2.1.0] - 2026-01-15

### Changed
- **Project relocation**: Moved plugin from E:\AI\Houdini_MCP to D:\2026\Q1\Houdini\Plugins\spacemouse_network_pan
- Updated all path references across 22+ locations in source files, documentation, and Houdini shelf tools
- Modified start_spacemouse_pan.bat to use self-contained directory structure
- Standardized all file line endings to Windows CRLF format

### Added
- houdini21.0/toolbar/spacemouse.shelf - Shelf tool definition included in repository
- Self-contained .venv virtual environment within plugin directory

### Fixed
- Fixed missing spacemouse_control and spacemouse_config tools in shelf toolbar
- Resolved mixed line ending issues (LF/CRLF) in batch and Python files

### Technical Notes
- Plugin is now fully self-contained and portable
- Houdini 21.0 compatible
