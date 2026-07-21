# Drydock Slash Command Guide

This guide explains how to use the available slash commands to enhance your coding and project management experience with Drydock.

## 📚 Knowledge Base (GraphRAG)
Drydock can build a local knowledge base from your documentation, code, and specs. This allows the agent to have deep, project-specific context.

| Command | Description | Example |
| :--- | :--- | :--- |
| `/graphrag build <path>` | Initializes and builds a knowledge base from a file or folder. | `/graphrag build ./docs` |
| `/graphrag add <path>` | Adds new files or folders to an existing knowledge base. | `/graphrag add ./src/api` |
| `/graphrag query <q>` | Manually test a query against the knowledge base. | `/graphrag query "How is auth handled?"` |
| `/graphrag status` | Checks the current state of the index. | `/graphrag status` |
| `/graphrag clear` | Wipes the knowledge base to start over. | `/graphrag clear` |

**Pro Tip:** Once built, Drydock automatically uses the `Knowledge` tool to draw on this data during your conversations.

---

## 🛠️ Custom Skills
Custom skills allow you to save complex prompts as reusable commands.

| Command | Description | Example |
| :--- | :--- | :--- |
| `/skills new <name> <prompt>` | Creates a new skill. Use `$ARGS` where you want to insert input. | `/skills new review "Review this code for security flaws: $ARGS"` |
| `/skills` | Lists all your created custom skills. | `/skills` |

**How to use a skill:** After creating `/skills new review ...`, you can simply type `/review [code or file path]` to trigger that prompt.

---

## ⚙️ System & Session Management
Commands to control the environment, model, and conversation flow.

### Environment & Model
- `/model`: Change the active LLM model or endpoint.
- `/cwd`: Print the current working directory.
- `/mcp`: List connected Model Context Protocol (MCP) servers.
- `/status`: Show the current session status.

### Conversation Control
- `/undo`: Revert the last file write operation.
- `/back`: Rewind the conversation by one turn.
- `/compact`: Shrink the conversation context to save tokens/memory.
- `/loop <n> <prompt>`: Repeat a specific prompt `n` times.
- `/clear`: Clear the current session history.

### General
- `/help`: Display the help menu.
- `/quit`: Exit the session.
