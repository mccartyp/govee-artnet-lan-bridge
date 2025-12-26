# Phase 3B: Code Deduplication and Architecture Simplification

This PR implements the second part of Phase 3 of the CLI Framework Improvement Plan, focusing on eliminating code duplication and simplifying the shell architecture.

## Changes Implemented

### 1. Extract Shared API Logic to Common Functions (3.3)

**Added 4 shared API helper functions in `cli.py:558-634`:**

1. **`_api_get(client, endpoint, config)`** - Generic GET handler
   - Consolidates repeated pattern: `_handle_response(client.get(...)) + _print_output(...)`
   - Used by 7+ operations (health, status, devices list, mappings list, etc.)

2. **`_api_get_by_id(client, endpoint, resource_id, config)`** - GET with resource ID
   - Handles GET requests with dynamic resource identifiers
   - Used by mappings get operation

3. **`_device_set_enabled(client, device_id, enabled, config)`** - Device enable/disable
   - Consolidates device enabled state PATCH operations
   - Used by 4 operations (CLI enable/disable, shell enable/disable)

4. **`_api_delete(client, endpoint, resource_id, config, custom_output)`** - Generic DELETE
   - Handles DELETE operations with optional custom output
   - Used by mappings delete operations

**Updated CLI commands to use shared helpers:**
- `_cmd_health()`, `_cmd_status()`, `_cmd_devices_list()` → Use `_api_get()`
- `_cmd_devices_enable()`, `_cmd_devices_disable()` → Use `_device_set_enabled()`
- `_cmd_mappings_list()`, `_cmd_mappings_channel_map()` → Use `_api_get()`
- `_cmd_mappings_get()` → Uses `_api_get_by_id()`
- `_cmd_mappings_delete()` → Uses `_api_delete()`

**Updated shell commands to use shared helpers:**
- `do_status()`, `do_health()` → Use `_api_get()`
- `do_devices()` → Uses `_api_get()` and `_device_set_enabled()`
- `do_mappings()` → Uses `_api_get()`, `_api_get_by_id()`, `_api_delete()`

**Before (duplicated code):**
```python
# In CLI
def _cmd_health(config, client, args):
    data = _handle_response(client.get("/health"))
    _print_output(data, config.output)

# In Shell
def do_health(self, arg):
    data = _handle_response(self.client.get("/health"))
    _print_output(data, self.config.output)
```

**After (shared helper):**
```python
# Shared helper
def _api_get(client, endpoint, config):
    data = _handle_response(client.get(endpoint))
    _print_output(data, config.output)
    return data

# In CLI
def _cmd_health(config, client, args):
    _api_get(client, "/health", config)

# In Shell
def do_health(self, arg):
    _api_get(self.client, "/health", self.config)
```

---

### 2. Simplify Shell Architecture - Remove cmd.Cmd (3.4)

**Removed cmd.Cmd inheritance:**
- Removed `import cmd` and inheritance from `cmd.Cmd`
- Shell now uses pure prompt_toolkit with custom command dispatch

**Implemented custom command dispatch:**
```python
# Command dispatch table (explicit, type-safe)
self.commands: dict[str, Callable[[str], Optional[bool]]] = {
    "connect": self.do_connect,
    "status": self.do_status,
    "help": self.do_help,
    "exit": self.do_exit,
    # ... all commands registered here
}
```

**Custom `onecmd()` implementation:**
```python
def onecmd(self, line: str) -> bool:
    """Execute a single command."""
    if not line or line.isspace():
        return False  # Handle empty lines
    
    # Parse command and arguments
    parts = line.split(maxsplit=1)
    command = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    
    # Dispatch to handler
    handler = self.commands.get(command)
    if handler:
        try:
            result = handler(arg)
            return result if result is not None else False
        except Exception as exc:
            self.console.print(f"[bold red]Error:[/] {exc}")
            return False
    else:
        print(f"Unknown command: {command}")
        return False
```

**Removed cmd.Cmd-specific methods:**
- `emptyline()` - Now handled in `onecmd()`
- `default()` - Unknown command handling in `onecmd()`
- No more `super().__init__()` call

**Benefits:**
- ✅ No dependency on cmd.Cmd module
- ✅ Full control over command dispatch
- ✅ Explicit command registry (easy to see all commands)
- ✅ Better integration with prompt_toolkit
- ✅ Type-safe command handlers
- ✅ Easier to extend and maintain

---

## Code Quality Improvements

**Duplication Reduction:**
- ✅ 9 CLI commands refactored to use shared helpers
- ✅ 4 shell commands refactored to use shared helpers
- ✅ ~40-50 lines of duplicated code eliminated
- ✅ Single source of truth for API operations

**Architecture Simplification:**
- ✅ Removed cmd.Cmd inheritance
- ✅ Explicit command dispatch with type safety
- ✅ Cleaner, more maintainable structure
- ✅ Better separation of concerns

**Maintainability:**
- ✅ Easier to add new API operations (add helper function)
- ✅ Easier to modify API patterns (change in one place)
- ✅ Clear command registration (explicit dispatch table)
- ✅ Better error handling

---

## Testing

- [x] Python syntax validation passed
- [x] All changes are backward compatible
- [x] Existing tests don't need updates (no cmd.Cmd dependencies)
- [x] Command dispatch works with existing test mocks

---

## Impact

**Files Modified:** 2 (cli.py, shell.py)  
**Lines Added:** ~150  
**Lines Removed:** ~90  
**Net Change:** +60 lines  
**Code Duplication Eliminated:** ~40-50 lines  
**Helper Functions Added:** 4  
**Commands Refactored:** 13  

---

## Related

- Part of CLI Framework Improvement Plan (Phase 3 - Part 2)
- Addresses items 3.3 and 3.4 from CLI_FRAMEWORK_REVIEW.md
- Follows Phase 3 Part 1 (error handling, constants, entry point)
- User selected approaches:
  - 3.3: Option A - Extract to common functions ✅
  - 3.4: Option B - Use prompt_toolkit directly ✅

---

## Next Steps

After Phase 3B merge:
- **Phase 4:** Security & Robustness
- **Phase 5:** Configuration & Documentation
- **Phase 6:** Performance Optimizations
- **Phase 7:** Advanced Features
