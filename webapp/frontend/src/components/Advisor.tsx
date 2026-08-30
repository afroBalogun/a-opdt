import { useState } from "react";
import { askAdvisor } from "../session";
import { Button, Eyebrow, Fine } from "./Type";

/**
 * Ask-box over the twin's advisor endpoint.
 *
 * Deliberately not a chat transcript. The advisor answers from the twin's
 * current state, so a scrollback of replies from ten minutes ago invites
 * reading stale numbers as current ones. One question, one answer, visibly
 * tied to now.
 */
const SUGGESTIONS = [
  "How is the crop doing right now?",
  "Should I irrigate today?",
  "Which readings come from real sensors?",
  "When does the next growth stage start?",
];

export function Advisor() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(q: string) {
    const text = q.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      const reply = await askAdvisor(text);
      setAnswer(reply.answer);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The advisor did not answer.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="bg-paper border border-ink/15 p-[clamp(1.25rem,3vw,2rem)]">
      <Eyebrow>Ask the twin</Eyebrow>

      <form
        className="mt-4 flex flex-wrap gap-3"
        onSubmit={(e) => { e.preventDefault(); void submit(question); }}
      >
        <input
          className="flex-1 min-w-[16rem] border border-ink/20 bg-transparent px-3 py-2
                     text-[0.9375rem] outline-none focus:border-ink/50"
          placeholder="Ask about the crop, irrigation, or a reading…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={busy}
          aria-label="Question for the advisor"
        />
        <Button type="submit" disabled={busy || !question.trim()}>
          {busy ? "Thinking…" : "Ask"}
        </Button>
      </form>

      {!answer && !error && !busy && (
        <div className="mt-4 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => { setQuestion(s); void submit(s); }}
              className="border border-ink/15 px-3 py-1.5 text-[0.8125rem] text-ink-soft
                         hover:border-ink/40 hover:text-ink transition-colors"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {busy && (
        <Fine className="mt-4">Reading the twin's current state…</Fine>
      )}

      {error && (
        <div className="mt-4 border border-ink/15 border-l-[3px] border-l-crit px-4 py-3
                        text-[0.8125rem] text-ink-soft">
          {error}
        </div>
      )}

      {answer && (
        <div className="mt-4 border-l-[3px] border-l-ink/40 pl-4">
          <p className="text-[0.9375rem] leading-relaxed whitespace-pre-wrap">{answer}</p>
          {/* Four of nineteen fields are measured on this hardware; the rest are
              stage nominals. The model is told to flag when it leans on one,
              but the reader should know the caveat exists regardless. */}
          <Fine className="mt-3">
            Answers come from the twin's current readings. Fields without a
            sensor are stage nominals, not observations.
          </Fine>
        </div>
      )}
    </section>
  );
}
