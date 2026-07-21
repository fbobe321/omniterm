# OmniTerm Design Proposals & Roadmap

This document outlines the architectural changes required to implement advanced features inspired by MobaXterm and iTerm2.

## 1. Split Screen (Multi-pane) Layout System
**Objective**: Allow users to view multiple terminal sessions simultaneously in a grid.

### Architectural Changes:
- **UI Component**: Replace `QTabWidget` in `MainWindow` with a custom `PaneManager` widget.
- **Implementation**:
    - Use `QSplitter` (nested horizontally and vertically) to create a recursive grid.
    - Each "Pane" will be a wrapper around `TerminalTab`.
    - **Pane State**: Maintain a tree structure of splitters to allow users to "Split Right" or "Split Down" on the currently focused pane.
- **Focus Management**: Implement a focus ring or highlight to indicate which pane is receiving keyboard input.

## 2. SSH Macro/Script Runner
**Objective**: Automate repetitive command sequences across sessions.

### Architectural Changes:
- **Data Schema**: Add a `macros` section to `sessions.json`:
  ```json
  "macros": [
    {
      "name": "Check Logs",
      "commands": ["cd /var/log", "tail -f syslog"],
      "delays": [0, 1.0]
    }
  ]
  ```
- **Worker Integration**: Add a `send_macro(macro_id)` method to `SSHWorker` and `LocalPTYWorker`.
- **Execution**: Use a `QTimer` within the worker to send commands sequentially with the specified delays to avoid overwhelming the remote shell.

## 3. Session Grouping (Folder System)
**Objective**: Organize hundreds of sessions into logical folders.

### Architectural Changes:
- **Data Schema**: Transition `sessions` from a list to a recursive structure:
  ```json
  "sessions": [
    {
      "type": "folder",
      "name": "Production Servers",
      "children": [ { "type": "ssh", "name": "Web01", ... } ]
    }
  ]
  ```
- **UI Integration**: Update `SessionDock.load_sessions_into_tree` to recursively traverse the `children` array and create `QStandardItem` parents.

## 4. Protocol Expansion (Mosh/RDP)
**Objective**: Support more remote access protocols.

### Implementation Paths:
- **Mosh**: Integrate a `MoshWorker` that wraps the `mosh-client` binary via `subprocess` and pipes stdout/stdin to the `PyBridge`.
- **RDP**: Embed a `QWebEngineView` instance of a web-based RDP client (like Apache Guacamole) or integrate a native C++ RDP library via PyQt bindings.
