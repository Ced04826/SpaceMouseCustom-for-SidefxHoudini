# Lessons Learned: Developing a Houdini Plugin with MCP Tools

Knowledge transfer for agents continuing HoudiniMCP development.

---

## Finding the Architecture

### What Failed
- Running hardware code directly inside Houdini (driver conflicts)
- Using Qt timers for polling (conflicts with Houdini's event loop)

### What Worked
Human provided reference material showing how other plugins handle external input. Key insight: **separate hardware reader from Houdini entirely**.

Final pattern: `External Process → UDP → Houdini event loop callback`

**Lesson:** When fighting Houdini's environment (drivers, Qt, threading), move the problematic part to an external process. Houdini is good at receiving data; not always good at acquiring it.

---

## MCP Tools Assessment

### Good For
- `execute_houdini_code` - Prototyping, testing APIs, debugging with `inspect.getsource()`
- `capture_pane` - Visual verification of changes
- `get_scene_info` - Understanding scene structure

### Not Good For
- **Iterative algorithm development** - Easy to fall into "try things" loop instead of understanding the problem
- **Hardware/driver issues** - Need external research and human guidance

---

## Process Mistakes to Avoid

### The Test Loop Trap
Made 10+ similar MCP calls testing rotation without stepping back. Human had to intervene.

**Rule:** After 5 similar test iterations without progress, STOP. Write out what you're seeing, what you expect, and try a fundamentally different approach.

### Not Consulting Other Agents Early
For complex math (rotations, matrices), should have consulted Codex/Gemini earlier instead of iterating blindly.

### Poor Testing Methodology
Testing at arbitrary states instead of systematic ones. Always reset to known state between tests.

---

## Key Gotchas

1. **Hot reload doesn't update running instances** - Classes defined inside functions keep old code after `importlib.reload()`. Must stop and restart.

2. **Houdini 21+ uses PySide6** - Not PySide2.

3. **Use `hou.ui.addEventLoopCallback()`** - More reliable than Qt timers for periodic tasks.

All documented in `.kiro/steering/houdini-plugin-development.md`.

---

## Recommendations

1. **Research first** - Search for how others solved similar problems before coding
2. **Separate concerns early** - External process + IPC when fighting Houdini's environment
3. **Ask for help on math** - Consult other agents before iterating on complex algorithms
4. **Test systematically** - Reset state, test at defined positions, verify one thing at a time

---

## Summary

MCP tools enable rapid iteration, but that can become a trap. Research and thinking beat blind testing.
