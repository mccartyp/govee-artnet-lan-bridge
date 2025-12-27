# Shell Architecture Refactor Plan

## Problem Statement

The CLI shell currently uses `PromptSession` for input and direct `console.print()` calls for output. This approach is incompatible with prompt_toolkit's Application model, causing output to overwrite the bottom toolbar regardless of pagination settings.

## Root Cause

When using PromptSession with a `bottom_toolbar`, the toolbar is rendered in a reserved space. However, direct calls to `console.print()` and `sys.stdout.write()` bypass prompt_toolkit's screen management system, writing directly to the terminal and overwriting the toolbar.

## Required Solution: Application-Based Architecture

### Current Architecture (Broken)
```
PromptSession
├── Input via session.prompt()
├── Output via console.print() → stdout (bypasses prompt_toolkit)
└── Toolbar via bottom_toolbar parameter
```

### Target Architecture (Correct)
```
Application
├── Layout (HSplit)
│   ├── TextArea (output - scrollable, read-only)
│   ├── Window (separator)
│   ├── BufferControl (input with autocomplete/history)
│   ├── Window (separator)
│   └── FormattedTextControl (toolbar)
├── Key bindings (Ctrl+C, Ctrl+D, Ctrl+L, Enter)
└── Event loop
```

## Implementation Steps

### 1. Update Imports
Add prompt_toolkit Application components:
- `Application`, `Layout`, `HSplit`, `Window`
- `TextArea`, `Buffer`, `BufferControl`
- `FormattedTextControl`, `KeyBindings`

### 2. Modify `__init__` Method
- Create `TextArea` for scrollable output (replaces direct printing)
- Create `Buffer` with history and autocomplete for input
- Set up key bindings for Ctrl+C, Ctrl+D, Ctrl+L
- Build `HSplit` layout with output pane, input field, and toolbar
- Create `Application` instance (replaces `PromptSession`)

### 3. Create Output Router: `_append_output()`
```python
def _append_output(self, text: str) -> None:
    """Append text to output TextArea using Rich formatting."""
    # Render text using Rich Console to StringIO buffer
    buffer = StringIO()
    temp_console = Console(file=buffer, force_terminal=True, width=self.console.width)
    temp_console.print(text, end="")

    # Append formatted text to output area
    self.output_area.text += buffer.getvalue()

    # Scroll to bottom and trigger redraw
    self.output_area.buffer.cursor_position = len(self.output_area.text)
    self.app.invalidate()
```

### 4. Create Input Handler: `_accept_input()`
```python
def _accept_input(self, buffer: Buffer) -> bool:
    """Handle command execution when user presses Enter."""
    line = buffer.text
    buffer.reset()

    # Echo command
    self._append_output(f"{self.prompt}{line}\n")

    # Process command
    if line and not line.isspace():
        line = self.precmd(line)
        stop = self.onecmd(line)
        if stop:
            self.app.exit(result=True)

    return False
```

### 5. Replace All `console.print()` Calls
Replace approximately 120+ occurrences of:
- `self.console.print(...)` → `self._append_output(...\n)`
- Ensure newlines are added where appropriate
- Handle Rich Table objects correctly (pass to _append_output)
- Handle special cases like `console.rule()`, `console.status()`, `console.clear()`

### 6. Update `cmdloop()` Method
```python
def cmdloop(self, intro: Optional[str] = None) -> None:
    """Run the Application event loop."""
    # Show intro in output area
    if intro is None:
        self._append_output("[bold cyan]Govee ArtNet Bridge - Interactive Shell[/]\n")
        self._append_output(f"[dim]Version {SHELL_VERSION}[/]\n\n")
        # ... tips ...

    # Run the application
    self.app.run()
```

### 7. Update `do_console()` Command
Add auto-pagination status display:
```python
if not args or (len(args) == 1 and args[0] == "pagination"):
    # Show current pagination setting
    if self.config.page_size is None:
        status = "[yellow]disabled[/]"
    else:
        auto_str = " [dim](auto-detected)[/]" if self.auto_pagination else ""
        status = f"[green]{self.config.page_size} lines[/]{auto_str}"

    self._append_output(f"Pagination: {status}\n")
    return
```

### 8. Implement SIGWINCH Fallback
For systems without SIGWINCH support, add periodic terminal size checks:
```python
def _check_terminal_size(self) -> None:
    """Check if terminal size changed and update pagination if needed."""
    if self.auto_pagination:
        import shutil
        terminal_height = shutil.get_terminal_size().lines
        new_page_size = max(10, terminal_height - 5)
        if new_page_size != self.config.page_size:
            self._update_page_size(new_page_size)

# Call this at the start of each command execution in onecmd()
```

### 9. Handle Special Output Cases

#### Watch Mode
Use `run_in_terminal()` for commands that need temporary terminal control:
```python
from prompt_toolkit.application import run_in_terminal

def do_watch(self, arg: str) -> None:
    def watch_loop():
        while True:
            # Command execution
            ...

    run_in_terminal(watch_loop)
```

#### Log Streaming
WebSocket log streaming needs special handling since it's long-running:
- Either use `run_in_terminal()` for full-screen streaming
- Or append to output area in real-time with `call_from_executor()`

## Testing Plan

1. **Basic Commands**: Verify connect, status, devices, help work
2. **Toolbar**: Confirm toolbar remains visible during all output
3. **Scrolling**: Test that output pane scrolls correctly
4. **Pagination**: Verify auto-pagination status shows correctly
5. **Resize**: Test terminal resize handling (with and without SIGWINCH)
6. **History/Autocomplete**: Ensure input history and autocomplete still work
7. **Key Bindings**: Test Ctrl+C, Ctrl+D, Ctrl+L

## Estimated Effort

- Core architecture changes: 2-3 hours
- Replace all console.print() calls: 2-3 hours
- Testing and bug fixes: 1-2 hours
- **Total: 5-8 hours**

## Current Status

- ✅ Architecture designed
- ✅ Imports identified
- ✅ `_append_output()` method created
- ✅ `_accept_input()` method created
- ⏸️ `__init__` partially updated (Application created but needs integration)
- ❌ `console.print()` calls not yet replaced (120+ occurrences)
- ❌ `cmdloop()` not yet updated
- ❌ Console pagination command not yet updated with auto status
- ❌ SIGWINCH fallback not yet implemented
- ❌ Testing not yet done

## Next Steps

To complete this refactor:

1. Apply the `__init__` changes to create Application layout
2. Systematically replace all `console.print()` calls with `_append_output()`
3. Update `cmdloop()` to run `self.app.run()`
4. Add auto-pagination status to `do_console()`
5. Implement SIGWINCH fallback
6. Test thoroughly

## Alternative: Simpler Fix

If the full refactor is too time-consuming, a simpler workaround is to:
1. Keep PromptSession architecture
2. Reserve significantly more space for toolbar (e.g., 10+ lines)
3. Document that pagination page_size should account for toolbar

However, this is a **band-aid solution** and the toolbar overlap will still occur under certain conditions. The Application refactor is the **correct long-term solution**.
