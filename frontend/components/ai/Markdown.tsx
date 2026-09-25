import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renders AI-generated text as formatted Markdown. Deliberately does NOT use rehype-raw,
 * so any raw HTML in the response is shown as literal text rather than rendered — the
 * "safe settings" requirement (AI output is untrusted text, not markup to execute).
 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="ai-markdown text-sm text-foreground [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
