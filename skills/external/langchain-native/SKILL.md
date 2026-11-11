---
name: langchain-native
description: "Interoperabilitas LangChain Python pada Kwee: ekspor tool registry menjadi StructuredTool tanpa mengganti loop agent inti."
triggers: [langchain, structuredtool, runnable, agent framework, integrasi agent, interoperabilitas, tool adapter]
priority: 65
---
# LangChain Native Adapter

Kwee keeps its native agent loop as the authority for WhatsApp sessions, tenant context, role allowlists, audit hooks, progress events, and tool execution. The optional LangChain adapter is an interoperability layer, not a second autonomous agent loop.

## Usage
- Check `intelligence_status` before assuming LangChain is installed.
- Convert already-authorized Kwee registry entries with `integrations.langchain_adapter.to_langchain_tools(...)`.
- Never export tools that were not already allowed for the current role.
- Do not invoke tool functions while merely constructing `StructuredTool` definitions.
- Preserve the same JSON schema and return shape. Catch adapter failures and fall back to the native Kwee registry.
- Use `langchain-openai` only when the integration explicitly needs its provider; the adapter itself must not force a provider or API key.

## Scope
Use LangChain for integrations and compatible tool ecosystems when there is a concrete benefit. Do not wrap every native tool or replace Kwee's current loop just to add a framework dependency.
