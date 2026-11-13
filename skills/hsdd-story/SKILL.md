---
name: hsdd-story
description: Use HSDD Story to create or maintain the development direction graph for a project, read its current requirements and progress, or synchronize implementation, evidence, and human decisions through its MCP server.
---

# HSDD Story

Use the connected HSDD Story tools for the workspace named by the user. Confirm the project identity from the read response before applying edits.

- Start with `story_read` overview. Read the relevant requirement and selected nodes for actual details. If the user describes a node without an ID, use the search view with a keyword and optional requirement, type, or state filters. Use the changes view with a prior version and token when available. Read the current overview again if the server asks for a refresh.
- When the user pastes a HSDD Story node reference, call `story_read` with the copied `nodeRef` as its only argument. Use the MCP address in the copied text to select the connection. If the server reports a workspace mismatch, ask for the matching connection. For archived nodes, read the original and follow the returned replacement IDs when the task concerns current work.
- Use `story_schema` with `types` or `operations` to look up the fields needed for the current edit. Create the project only when the user asks to begin a graph and the bound workspace is empty.
- Use requirements for independently readable work and nodes for steps that can advance, be checked, or be handed off independently. Encode prerequisites as `requires` from prerequisite to successor. Use references for supporting context and follow-up edges for work outside the current delivery.
- Make the next action and its actor clear when work is blocked or a person needs to respond. Keep summaries brief and link evidence rather than copying logs.
- Attach diagrams, screenshots, or longer explanations when they make a node easier to understand. Use `attachment.add` in `story_apply`. Supply `sourcePath` for a workspace file, `text` for Markdown, or `dataBase64` for encoded content. Read attachments by ID with `story_read_attachment`. Keep the current conclusion in the node, and carry its `attachmentRefs` to the appropriate new nodes during a split or merge.
- Record work and outcomes independently. A completed implementation does not establish test success or business acceptance. Preserve failed and skipped checks and mark affected old conclusions for review. Save genuine chat input in the person’s original words and put your interpretation in the node body. Formal acceptance comes from the webpage. Before setting an acceptance node to finished and satisfied, read its latest decision and include the current approval ID from `display.acceptance.inputId` in `humanInputRefs`. Ask the person to submit or reconfirm approval in the webpage when required.
- Submit related changes with `story_apply` using the read revision and token. Correct a node in place. When merging, splitting, or retiring it, account for active edges, completion gates, evidence, and human input references in the same batch. On a conflict, inspect `changedIds`, `overlappingIds` and `retiredIds`, then re-read the relevant nodes and reconcile. When `refreshRequired` is true, re-read the overview and relevant subgraph. Acknowledge synchronization after the service reports success.
- Save meaningful changes to requirements, implementation, verification, decisions, blockers or structure. Group related changes, and save milestones during longer work. Leave the graph untouched for ordinary discussion with no material changes.

The graph guides communication and planning. Use the agent's normal tools and the current task's authorization for coding, testing and delivery. If the workspace's MCP tools are unavailable, report that the graph could not be synchronized.
