import Link from "next/link";

export function AppNav() {
  return (
    <header
      data-pkrtask-app-nav
      className="sticky top-0 z-40 flex h-14 items-center gap-6 border-b border-line bg-surface px-5"
    >
      <Link href="/" className="text-lg font-semibold text-ink">
        <span className="text-highlight">Pkr</span>task
      </Link>
      {/* A plain anchor: the scrimmage site is outside Pkrtask's base path, which <Link> would add. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a href="/" className="ml-auto text-sm text-ink-muted hover:text-ink">
        ← Scrimmage
      </a>
    </header>
  );
}
