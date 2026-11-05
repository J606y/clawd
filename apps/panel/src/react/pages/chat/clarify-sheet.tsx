import { useEffect, useRef, useState } from "react";
import { clarifyAnswers, clarifyIdentity, type ClarifyPrompt } from "@/lib/clarify";

export function ClarifySheet({
  prompt,
  onSubmit,
}: {
  prompt: ClarifyPrompt;
  onSubmit: (answers: { requestId: string; answer: string }[]) => void;
}) {
  const identity = clarifyIdentity(prompt);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<number[][]>(() => prompt.questions.map(() => []));
  const [texts, setTexts] = useState<string[]>(() => prompt.questions.map(() => ""));
  const [collapsed, setCollapsed] = useState(false);
  const [seen, setSeen] = useState(identity);
  const inputRef = useRef<HTMLInputElement>(null);
  const shownPage = useRef(-1);
  if (seen !== identity) {
    setSeen(identity);
    setPage(0);
    setSelected(prompt.questions.map(() => []));
    setTexts(prompt.questions.map(() => ""));
    setCollapsed(false);
    shownPage.current = -1;
  }
  const question = prompt.questions[Math.min(page, prompt.questions.length - 1)];
  useEffect(() => {
    const input = inputRef.current;
    if (!input || shownPage.current === page) return;
    input.value = texts[page] ?? "";
    shownPage.current = page;
  }, [page, texts]);
  if (!question) return null;
  const total = prompt.questions.length;
  const last = page >= total - 1;
  const picked = new Set(selected[page] ?? []);
  const ready = Boolean(clarifyAnswers({ ...prompt, questions: [question] }, [selected[page] ?? []], [texts[page] ?? ""]));
  const answers = clarifyAnswers(prompt, selected, texts);
  const hide = () => setCollapsed(true);
  const go = (next: number) => {
    if (next < 0 || next >= total || prompt.pending) return;
    setPage(next);
  };
  const submit = () => {
    if (prompt.pending) return;
    if (!last) {
      if (clarifyAnswers(prompt, selected, texts)?.[page]) go(page + 1);
      return;
    }
    if (!answers) return;
    onSubmit(answers);
  };
  if (collapsed) {
    return (
      <div className="clarify collapsed">
        <button className="reopen" type="button" onClick={() => setCollapsed(false)}>继续回答</button>
      </div>
    );
  }
  return (
    <div className="clarify">
      <div className="scrim" onClick={hide} />
      <section
        className="card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="clarify-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            hide();
          }
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={total > 1 ? "nav" : "head"}>
          {total > 1 ? (
            <button className="icon" type="button" aria-label="上一题" disabled={page === 0 || prompt.pending} onClick={() => go(page - 1)}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
          ) : null}
          <h2 className="title" id="clarify-title">{total > 1 ? `第 ${page + 1} 题，共 ${total} 题` : question.question}</h2>
          {total > 1 ? (
            <button className="icon" type="button" aria-label="下一题" disabled={last || prompt.pending} onClick={() => go(page + 1)}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
          ) : null}
          <button className="close" type="button" aria-label="关闭" onClick={hide}>
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 3l8 8M11 3 3 11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
          </button>
        </div>
        {total > 1 ? <p className="question">{question.question}</p> : null}
        {question.multiSelect ? <p className="hint">可多选</p> : null}
        {question.choices.length ? (
          <div className="options" role={question.multiSelect ? "group" : "radiogroup"} aria-label={question.question}>
            {question.choices.map((choice, index) => (
              <button
                key={choice.label}
                className="option"
                type="button"
                role={question.multiSelect ? "checkbox" : "radio"}
                aria-checked={picked.has(index)}
                disabled={prompt.pending}
                onClick={() => {
                  setSelected((state) => state.map((row, rowIndex) => {
                    if (rowIndex !== page) return row;
                    if (!question.multiSelect) return row[0] === index ? [] : [index];
                    return row.includes(index) ? row.filter((item) => item !== index) : [...row, index];
                  }));
                }}
              >
                {choice.label}{choice.recommended ? <span className="tag">推荐</span> : null}
              </button>
            ))}
          </div>
        ) : null}
        <input
          ref={inputRef}
          placeholder="输入您的答案..."
          enterKeyHint="done"
          autoComplete="off"
          disabled={prompt.pending}
          onChange={(event) => setTexts((state) => state.map((value, index) => index === page ? event.target.value : value))}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            submit();
          }}
        />
        {prompt.error ? <p className="error" role="alert">{prompt.error}</p> : null}
        <div className="foot">
          <button className="next" type="button" disabled={prompt.pending || (last ? !answers : !ready)} onClick={submit}>
            {prompt.pending ? "提交中" : last ? "提交" : "下一页"}
            {!prompt.pending && !last ? <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 7h8M8 3l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg> : null}
          </button>
        </div>
      </section>
    </div>
  );
}
