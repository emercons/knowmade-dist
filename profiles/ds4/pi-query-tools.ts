import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

const maxTokens = Number(process.env.DS4_WIKI_MAX_TOKENS || 2048);

const protocol = `# DS4 Read-Only Wiki Protocol

Be terse, literal, and tool-first.

- Use read, grep, find, and ls as actual tool calls. Never print tool-call JSON.
- Inspect named paths instead of claiming filesystem access is unavailable.
- Read the wiki index before exact evidence files. Search only when indexes fail.
- Never edit, write, delete, move, run shell commands, or propose a write as done.
- Do not narrate hidden reasoning or begin with "Let me" or "I need to".
- Continue tool use until the answer is grounded, then give a concise answer with exact file paths.
- If the wiki lacks the answer, say so. Never fill a gap from model memory.`;

export default function ds4WikiQuery(pi: ExtensionAPI) {
	pi.on("before_agent_start", (event) => {
		if (event.systemPrompt.includes("# DS4 Read-Only Wiki Protocol")) return;
		return { systemPrompt: `${event.systemPrompt}\n\n${protocol}` };
	});

	pi.on("before_provider_request", (event) => {
		if (typeof event.payload !== "object" || event.payload === null) return;
		const payload = event.payload as Record<string, unknown>;
		const requested = typeof payload.max_tokens === "number" ? payload.max_tokens : maxTokens;
		return {
			...payload,
			temperature: 0,
			max_tokens: Math.min(requested, maxTokens),
			...(Array.isArray(payload.tools) && payload.tools.length > 0
				? { tool_choice: payload.tool_choice ?? "auto" }
				: {}),
		};
	});
}
