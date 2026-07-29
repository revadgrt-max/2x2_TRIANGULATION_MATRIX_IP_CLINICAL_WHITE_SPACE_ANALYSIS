export function AuroraBackground() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-[#05070d]">
      <div
        className="absolute left-1/4 top-0 h-[560px] w-[560px] rounded-full bg-sky-500/20 blur-[120px]"
        style={{ animation: "drift-a 22s ease-in-out infinite" }}
      />
      <div
        className="absolute bottom-0 right-1/4 h-[520px] w-[520px] rounded-full bg-fuchsia-500/15 blur-[130px]"
        style={{ animation: "drift-b 26s ease-in-out infinite" }}
      />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_0%,rgba(255,255,255,0.06),transparent_60%)]" />
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.5) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.5) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
        }}
      />
    </div>
  );
}
