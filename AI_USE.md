# AI coding tools disclosure

- OpenAI Codex was used in the development session to review the assessment against repository code, design and implement the initial timeline-engine/provider-configuration changes, populate dependency and project documentation, and explain remaining gaps.
- OpenAI Codex was also used to add Pydantic Settings configuration, correlation ID handling, and Redis-backed idempotent response caching for the prompt interpretation endpoint.
- OpenAI Codex was used to add versioned session storage, optimistic revision checks, and HTTP endpoints for applying typed timeline operations to prepared transcripts.
- OpenAI Codex was used to connect session prompts to transcript context and add a deterministic exact-phrase/time-based mock interpreter for keyless development.
- No AI-generated video, audio, training data, or source media is included in this repository by these changes.
- The application's configured inference model is Groq-hosted `openai/gpt-oss-20b`; it is separate from the AI coding assistant disclosure above.
