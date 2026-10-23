/** Small line icons in the spirit of SF Symbols (1.6px stroke, 20px grid). Decorative only. */
const PATHS = {
  compose: "M4 16h3l8.5-8.5a2.1 2.1 0 0 0-3-3L4 13v3zM11.5 5.5l3 3",
  chart: "M4 16V9M8.5 16V5M13 16v-5M17 16V7",
  gear:
    "M10 12.6a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2zM16 10a6 6 0 0 0-.1-1l1.6-1.3-1.6-2.7-1.9.7a6 6 0 0 0-1.7-1L12 2.8H8.8l-.3 2a6 6 0 0 0-1.7 1l-1.9-.8-1.6 2.7L4.9 9a6 6 0 0 0 0 2l-1.6 1.3 1.6 2.7 1.9-.7a6 6 0 0 0 1.7 1l.3 1.9H12l.3-2a6 6 0 0 0 1.7-1l1.9.8 1.6-2.7-1.6-1.3c.1-.3.1-.7.1-1z",
  sun: "M10 13.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM10 2v1.5M10 16.5V18M2 10h1.5M16.5 10H18M4.3 4.3l1.1 1.1M14.6 14.6l1.1 1.1M4.3 15.7l1.1-1.1M14.6 5.4l1.1-1.1",
  moon: "M16.5 12.2A6.8 6.8 0 0 1 7.8 3.5a6.8 6.8 0 1 0 8.7 8.7z",
  auto: "M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM10 3v14",
  search: "M8.8 14.6a5.8 5.8 0 1 0 0-11.6 5.8 5.8 0 0 0 0 11.6zM13 13l4 4",
  folder: "M2.8 5.5c0-.8.7-1.5 1.5-1.5h3.2l1.6 1.8h6.6c.8 0 1.5.7 1.5 1.5v7.2c0 .8-.7 1.5-1.5 1.5H4.3c-.8 0-1.5-.7-1.5-1.5V5.5z",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg className={`icon${className ? ` ${className}` : ""}`} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d={PATHS[name]} />
      {name === "auto" && <path className="icon-fill" d="M10 3a7 7 0 0 0 0 14z" />}
    </svg>
  );
}
