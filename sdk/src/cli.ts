#!/usr/bin/env node
/**
 * navigator — compile, push, and verify demo flows.
 *
 * `verify` is the one that earns the package: it runs your flows against your own
 * dev server and exits non-zero when a postcondition fails. Put it in CI and a
 * broken demo fails the build instead of failing in front of a prospect.
 */

import { readFile } from "node:fs/promises";
import { writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

import { NavigatorApiError, NavigatorClient } from "./client.js";
import { compile } from "./compile.js";
import type { NavigatorConfig } from "./config.js";

const USAGE = `navigator <command> [options]

Commands:
  compile              Compile the config to site graph YAML on stdout
  push                 Compile and upload as a new revision
  verify [flow...]     Run flows against the API; exit 1 on any failure
                       Defaults to every flow in the config.

Options:
  -c, --config <path>  Config file (default: ./navigator.config.ts)
  -o, --out <path>     Write YAML here instead of stdout (compile only)
  -h, --help

Environment:
  NAVIGATOR_API_KEY    Required for push and verify
  NAVIGATOR_BASE_URL   Default http://localhost:8000

A .ts config needs a TypeScript loader:
  node --experimental-strip-types $(which navigator) push
Or compile it to .js first and pass --config navigator.config.js
`;

interface Args {
  command: string;
  config: string;
  out?: string;
  rest: string[];
}

function parseArgs(argv: string[]): Args {
  const args: Args = { command: "", config: "./navigator.config.ts", rest: [] };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]!;
    if (arg === "-h" || arg === "--help") {
      process.stdout.write(USAGE);
      process.exit(0);
    } else if (arg === "-c" || arg === "--config") {
      args.config = argv[++i] ?? args.config;
    } else if (arg === "-o" || arg === "--out") {
      args.out = argv[++i];
    } else if (!args.command) {
      args.command = arg;
    } else {
      args.rest.push(arg);
    }
  }
  return args;
}

async function loadConfig(path: string): Promise<NavigatorConfig> {
  const full = resolve(path);
  if (full.endsWith(".json")) {
    return JSON.parse(await readFile(full, "utf8")) as NavigatorConfig;
  }
  const module = await import(pathToFileURL(full).href);
  const config = module.default ?? module.config;
  if (!config) {
    throw new Error(`${path} has no default export`);
  }
  return config as NavigatorConfig;
}

/** Every (pageId, flowId) pair the config declares. */
function allFlows(config: NavigatorConfig): Array<[string, string]> {
  return Object.entries(config.pages).flatMap(([pageId, page]) =>
    Object.keys(page.flows).map((flowId) => [pageId, flowId] as [string, string]),
  );
}

async function cmdCompile(args: Args): Promise<number> {
  const yaml = compile(await loadConfig(args.config));
  if (args.out) {
    await writeFile(args.out, yaml);
    process.stderr.write(`wrote ${args.out}\n`);
  } else {
    process.stdout.write(yaml);
  }
  return 0;
}

async function cmdPush(args: Args): Promise<number> {
  const yaml = compile(await loadConfig(args.config));
  const revision = await NavigatorClient.fromEnv().pushSiteGraph(yaml);
  process.stdout.write(
    `pushed ${revision.site} revision ${revision.revision} ` +
      `(graph version ${revision.graph_version}, source ${revision.source})\n`,
  );
  return 0;
}

async function cmdVerify(args: Args): Promise<number> {
  const config = await loadConfig(args.config);
  const client = NavigatorClient.fromEnv();

  // Push first: verifying a stale revision on the server would prove nothing
  // about the config in the working tree.
  await client.pushSiteGraph(compile(config));

  const requested = args.rest.length
    ? resolveRequested(config, args.rest)
    : allFlows(config);

  if (requested.length === 0) {
    process.stderr.write("no flows to verify\n");
    return 1;
  }

  let failed = 0;
  for (const [pageId, flowId] of requested) {
    const started = await client.startDemo(pageId, flowId);
    const demo = await client.waitForDemo(started.demo_id);
    const ok = demo.status === "finished" && demo.failures === 0;
    if (!ok) failed++;

    process.stdout.write(
      `${ok ? "PASS" : "FAIL"}  ${pageId}/${flowId}  ` +
        `${demo.actions} action(s), ${demo.failures} failure(s)\n`,
    );

    if (!ok) {
      if (demo.error) process.stdout.write(`      error: ${demo.error}\n`);
      for (const entry of await client.demoActions(demo.demo_id)) {
        if (entry.verify?.passed === false || !entry.actual_result.ok) {
          const expects = entry.expected_postcondition;
          process.stdout.write(
            `      ${entry.tool_call.tool}: expected ${expects.check}` +
              `${expects.selector ? ` on ${expects.selector}` : ""}` +
              `${expects.expected ? ` == ${JSON.stringify(expects.expected)}` : ""}` +
              `, got: ${entry.verify?.actual ?? entry.actual_result.detail}\n`,
          );
        }
      }
    }
  }

  process.stdout.write(
    `\n${requested.length - failed}/${requested.length} flow(s) passed\n`,
  );
  return failed === 0 ? 0 : 1;
}

/** Accept `flow_id` or `page_id/flow_id`. */
function resolveRequested(
  config: NavigatorConfig,
  names: string[],
): Array<[string, string]> {
  const known = allFlows(config);
  return names.map((name) => {
    const match = name.includes("/")
      ? known.find(([p, f]) => `${p}/${f}` === name)
      : known.find(([, f]) => f === name);
    if (!match) {
      const list = known.map(([p, f]) => `${p}/${f}`).join(", ");
      throw new Error(
        `unknown flow ${JSON.stringify(name)}; config declares: ${list}`,
      );
    }
    return match;
  });
}

const COMMANDS: Record<string, (args: Args) => Promise<number>> = {
  compile: cmdCompile,
  push: cmdPush,
  verify: cmdVerify,
};

async function main(): Promise<number> {
  const args = parseArgs(process.argv.slice(2));
  const handler = COMMANDS[args.command];
  if (!handler) {
    process.stderr.write(USAGE);
    return args.command ? 1 : 0;
  }
  try {
    return await handler(args);
  } catch (error) {
    if (error instanceof NavigatorApiError) {
      // The server's validator message is the useful part; don't bury it.
      process.stderr.write(`error: ${error.detail}\n`);
    } else {
      process.stderr.write(`error: ${(error as Error).message}\n`);
    }
    return 1;
  }
}

main().then((code) => process.exit(code));
