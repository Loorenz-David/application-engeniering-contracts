# AI Architecture Contract

This contract defines the engineering rules for every AI agent and MCP server built on top of a backend application that follows the [`Backend_architecture/`](../Backend_architecture/README.md) contract set.

It is model-agnostic. The patterns here apply regardless of which LLM provider you use — OpenAI, Anthropic, Google, Mistral, or any other. Provider-specific adapters implement a common interface; everything above that interface is provider-neutral.

Agents and engineers **must** read this contract before writing any AI layer code. Every rule has a reason. The reason is stated so you can apply judgment to edge cases.

---

## How this contract is organized

### Foundation
| File | Covers |
|---|---|
| [01_ai_architecture.md](01_ai_architecture.md) | Layer map, folder structure, tech stack, hard dependency rules |
| [02_tool_contract.md](02_tool_contract.md) | Tool definition, input/output schema, naming, backend mapping |
| [03_agent_identity.md](03_agent_identity.md) | Auth, `ServiceContext` construction, service accounts, audit trail |

### MCP Server
| File | Covers |
|---|---|
| [04_mcp_server.md](04_mcp_server.md) | Embedded server setup, structure, capabilities, transport |
| [05_mcp_tools.md](05_mcp_tools.md) | MCP tool definitions, naming, error mapping, registration |
| [06_mcp_resources.md](06_mcp_resources.md) | Read-only data exposure, URI naming, resource handlers |
| [07_mcp_prompts.md](07_mcp_prompts.md) | Prompt template definitions, argument schemas, when to use |
| [08_mcp_auth.md](08_mcp_auth.md) | Client authentication, API key/JWT, identity flow into `ServiceContext` |

### Agent Patterns
| File | Covers |
|---|---|
| [09_single_agent.md](09_single_agent.md) | One LLM + tools — system prompt contract, tool loop, `AgentResult` return type, depth cap |
| [10_orchestrator.md](10_orchestrator.md) | Task decomposition, subagent delegation, knowledge resolver, research subagent registration |
| [11_subagent.md](11_subagent.md) | Bounded scope, input/output contract, clarification upward — never peer-to-peer |
| [12_human_in_loop.md](12_human_in_loop.md) | Confirmation gates (safety) vs intent clarification (understanding) |
| [19_clarification_protocol.md](19_clarification_protocol.md) | Mid-task uncertainty — decision tree, `ask_clarification` tool, depth limit, resume, propagation chain |

### Memory
| File | Covers |
|---|---|
| [13_context_management.md](13_context_management.md) | Window budget, what to keep vs discard, summarization strategy |
| [14_persistent_memory.md](14_persistent_memory.md) | DB-backed memory model, what to store, retrieval pattern |
| [15_semantic_memory.md](15_semantic_memory.md) | Embeddings, vector store, RAG query pattern |

### Quality & Operations
| File | Covers |
|---|---|
| [16_testing_agents.md](16_testing_agents.md) | Mock LLM, tool harness, deterministic golden-input tests |
| [17_safety_guardrails.md](17_safety_guardrails.md) | Dangerous action detection, required confirmation, scope bounding |
| [18_observability.md](18_observability.md) | Tracing tool calls, logging decisions, cost and token tracking |

### Multi-Modal
| File | Covers |
|---|---|
| [30_multimodal.md](30_multimodal.md) | File upload, `FileAttachment`, extraction tools (`extract_text`, `describe_image`, `extract_pdf_pages`), extraction agent pattern, `accepted_file_types` |

### Routing & Planning
| File | Covers |
|---|---|
| [26_router_agent.md](26_router_agent.md) | Intent classification, `IntentRegistry`, confidence threshold, entity extraction, Tier 1/2/3 dispatch |
| [27_planning_orchestrator.md](27_planning_orchestrator.md) | Tier 3 — structured plan generation, step execution, plan revision, synthesis |
| [28_conversation_session.md](28_conversation_session.md) | Multi-turn context — `ConversationSession`, active entities, history injection into router and agents |

### Production Readiness
| File | Covers |
|---|---|
| [29_provider_resilience.md](29_provider_resilience.md) | `ResilientProvider`, retry policy, circuit breaker, fallback provider, error taxonomy |
| [20_streaming.md](20_streaming.md) | `StreamEvent` types, `AgentRunner.stream()`, SSE transport, heartbeats |
| [21_prompt_versioning.md](21_prompt_versioning.md) | Version files, `PromptRegistry`, in-flight isolation, canary rollout, rollback |
| [22_background_tasks.md](22_background_tasks.md) | `AgentTask` model, event log, HTTP polling API, worker loop, async clarification resume |
| [23_cost_budget.md](23_cost_budget.md) | Per-workspace monthly token budgets, soft/hard limits, per-session token cap, `BudgetExceededError` |
| [24_agent_evaluation.md](24_agent_evaluation.md) | Core metrics, golden evaluation suite, A/B prompt comparison, deployment quality gates |
| [25_workspace_agent_config.md](25_workspace_agent_config.md) | Per-workspace model/prompt overrides, `system_prompt_suffix`, `AgentDisabledError`, canary pattern |

---

## Navigation matrix — what to read for each task

| Task | Start here | Then read |
|---|---|---|
| **Bootstrap the AI layer on a new app** | [01_ai_architecture.md](01_ai_architecture.md) | 03, 04, 08 |
| **Expose a backend command as an MCP tool** | [05_mcp_tools.md](05_mcp_tools.md) | 02, 03, 08 |
| **Expose domain data as an MCP resource** | [06_mcp_resources.md](06_mcp_resources.md) | 04 |
| **Add a reusable prompt template** | [07_mcp_prompts.md](07_mcp_prompts.md) | 04 |
| **Build a single-purpose agent** | [09_single_agent.md](09_single_agent.md) | 02, 03, 13 |
| **Build an orchestrator with subagents** | [10_orchestrator.md](10_orchestrator.md) | 11, 12, 13, 19 |
| **Build a subagent** | [11_subagent.md](11_subagent.md) | 02, 03 |
| **Add a confirmation gate for a dangerous tool** | [12_human_in_loop.md](12_human_in_loop.md) | 17 |
| **Handle mid-task uncertainty / agent asks a question** | [19_clarification_protocol.md](19_clarification_protocol.md) | 09, 10, 11, 12 |
| **Route free-text user requests to the right agent** | [26_router_agent.md](26_router_agent.md) | 09, 10, 19 |
| **Handle open-ended goals with unknown execution paths** | [27_planning_orchestrator.md](27_planning_orchestrator.md) | 10, 26 |
| **Maintain context across a multi-turn conversation** | [28_conversation_session.md](28_conversation_session.md) | 14, 26 |
| **Stream agent progress to the client** | [20_streaming.md](20_streaming.md) | 09, 22 |
| **Version and roll out a new system prompt** | [21_prompt_versioning.md](21_prompt_versioning.md) | 24, 25 |
| **Run an agent as a background job** | [22_background_tasks.md](22_background_tasks.md) | 09, 20 |
| **Set per-workspace token budgets** | [23_cost_budget.md](23_cost_budget.md) | 18, 25 |
| **Evaluate a prompt change before shipping** | [24_agent_evaluation.md](24_agent_evaluation.md) | 16, 21 |
| **Override agent config per workspace** | [25_workspace_agent_config.md](25_workspace_agent_config.md) | 21, 23, 24 |
| **Manage context across a long session** | [13_context_management.md](13_context_management.md) | 14 |
| **Store and recall agent decisions** | [14_persistent_memory.md](14_persistent_memory.md) | 13 |
| **Add semantic search / RAG** | [15_semantic_memory.md](15_semantic_memory.md) | 14 |
| **Write tests for an agent or tool** | [16_testing_agents.md](16_testing_agents.md) | 02, 09, 24 |
| **Add guardrails to a dangerous tool** | [17_safety_guardrails.md](17_safety_guardrails.md) | 02, 12 |
| **Add observability to the agent layer** | [18_observability.md](18_observability.md) | 09, 05 |
| **Handle LLM provider failures, rate limits, and outages** | [29_provider_resilience.md](29_provider_resilience.md) | 01, 18 |
| **Accept and process images or documents** | [30_multimodal.md](30_multimodal.md) | 02, 17, 28 |
| **Debug an auth or identity issue** | [03_agent_identity.md](03_agent_identity.md) | 08 |

---

## Scope boundary

This contract covers the **AI layer only**: agents, tools, MCP server, memory, orchestration, and observability.

It does not cover backend application structure, database schema, HTTP routing, or deployment. Those concerns live in [`Backend_architecture/`](../Backend_architecture/README.md).

The seam between the two sets is the backend service layer. Agent tools call existing commands and queries directly — the backend has no knowledge of the agent calling it. A command receives a `ServiceContext` whether it was triggered by an HTTP router, an agent tool, or an MCP client.

---

## Non-negotiable rules (memorize these)

1. **Tools own zero business logic.** A tool validates its input, builds a `ServiceContext`, calls one backend command or query, and returns the result. Nothing else.
2. **One tool = one backend operation.** A tool that calls two commands is two tools that need an agent to coordinate them.
3. **The backend has no imports from the AI layer.** The dependency arrow always points downward: AI → backend, never backend → AI.
4. **Tools never bypass the service layer.** No direct ORM queries from tool functions. All DB access goes through commands and queries.
5. **Every tool has a complete JSON Schema for its input.** No untyped parameters. The schema is the contract between the LLM and the tool.
6. **Agent identity is always explicit.** Every `ServiceContext` built by the AI layer carries a traceable actor: a user ID or a named service account. Anonymous agent calls are forbidden.
7. **Dangerous tools require a confirmation gate.** Any tool that deletes, overwrites, or sends an irreversible external message must check for explicit user confirmation before executing.
8. **Tools return structured data, never prose.** The LLM narrates; the tool returns JSON. Mixing the two makes testing impossible.
9. **Every LLM call is logged.** Model, token counts, latency, tool calls made, and the triggering session ID must be recorded before the call resolves.
10. **Research before escalating.** An agent must attempt to resolve uncertainty with its own tools before calling `ask_clarification`. Escalating without researching first is a contract violation.
11. **Clarification travels upward, never sideways.** Agents never contact peer agents directly. All cross-domain questions go through the orchestrator, which resolves or propagates them.
12. **`AgentRunner.run()` always returns `AgentResult`.** Callers branch on `result.status`. A plain string return is a contract violation.
13. **Prompt versions are immutable once deployed.** A deployed version is never edited in place. New behavior = new version file. In-flight sessions always resume on the version they started with.
14. **Token counts are the stored unit, not dollar amounts.** Store tokens; calculate cost on read. Pricing changes must not require data migrations.
15. **Budget checks happen before sessions start, never mid-flight.** Pre-flight enforcement gates new sessions. Running sessions are never killed by budget enforcement — that would corrupt agent state.
16. **All tool results are wrapped in structural delimiters before entering the message history.** This is the primary defense against prompt injection — data is never indistinguishable from instructions.
17. **Provider calls always go through `ResilientProvider`.** No bare provider adapter is used directly in production. Retry logic, circuit breaking, and fallback are infrastructure concerns, not agent concerns.
18. **The LLM never sees a file URL or storage path — only `file_id`.** All file access is mediated through tools that enforce workspace ownership. Extraction is infrastructure; domain reasoning is application-specific.
