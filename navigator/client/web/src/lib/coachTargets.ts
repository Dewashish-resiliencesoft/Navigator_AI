/** Map readiness check ids → Client dashboard tab + coach spotlight target. */

export type CoachTarget =
  | "graph-publish"
  | "graph-editor"
  | "settings-gemini"
  | "demo-domain"
  | "flows-playlist"
  | "flows-record"
  | "knowledge-explore"
  | "knowledge-editor";

export type CoachGuide = {
  checkId: string;
  tab: string;
  target: CoachTarget;
  title: string;
  tip: string;
};

const READINESS_COACH: Record<
  string,
  Omit<CoachGuide, "checkId">
> = {
  published: {
    tab: "graph",
    target: "graph-publish",
    title: "Publish site graph",
    tip: "Open Site graph. Save draft if you changed anything, then click Publish so live visitors use this revision.",
  },
  graph: {
    tab: "graph",
    target: "graph-editor",
    title: "Fix site graph",
    tip: "Edit the site graph YAML here — fix invalid pages/flows, then Save draft and Publish.",
  },
  live: {
    tab: "settings",
    target: "settings-gemini",
    title: "Add Gemini key",
    tip: "Select Google Gemini, paste your API key, then Save. Live meeting audio needs Gemini Live.",
  },
  live_url: {
    tab: "demo",
    target: "demo-domain",
    title: "Set product domain",
    tip: "Enter your real product URL (https://…) and Save domain. Fixture or example.com URLs block demos.",
  },
  offerable_flow: {
    tab: "flows",
    target: "flows-record",
    title: "Record a validated flow",
    tip: "Record a flow (or Update existing), then validate it. At least one offerable flow is required before demos.",
  },
  playlist: {
    tab: "flows",
    target: "flows-playlist",
    title: "Build the demo playlist",
    tip: "Add flows to the playlist in walkthrough order, then Save order. Empty playlist = nothing to show.",
  },
  guided_stubs: {
    tab: "flows",
    target: "flows-record",
    title: "Bind guided selectors",
    tip: "Choose Update existing for the guided flow and record clicks so selectors bind. Unbound plans only show home.",
  },
  knowledge: {
    tab: "knowledge",
    target: "knowledge-explore",
    title: "Add product knowledge",
    tip: "Run Product Explore to crawl, or write/save knowledge below so the agent can answer questions.",
  },
};

export function coachForCheck(checkId: string): CoachGuide | null {
  const base = READINESS_COACH[checkId];
  if (!base) return null;
  return { checkId, ...base };
}

export function hasCoach(checkId: string): boolean {
  return checkId in READINESS_COACH;
}
