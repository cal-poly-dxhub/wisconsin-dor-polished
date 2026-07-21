import { readFileSync } from 'fs';
import { join } from 'path';
import * as TOML from '@iarna/toml';

/**
 * Reads config/retrieval.toml (the source of truth for agentic_retrieval
 * Lambda env vars — see docs/spec-retrieval-pipeline-refactor.md) and
 * exposes its [env.*] defaults as a plain string map for CDK to use when
 * building a Lambda's environment block.
 *
 * This module only supplies *defaults*. Values passed explicitly by the
 * caller (e.g. table names, callback URLs resolved from other stacks'
 * outputs) always take precedence — see getRetrievalEnv()'s `overrides`
 * param.
 */

interface RetrievalEnvEntry {
  default: string | number | boolean;
  type?: string;
  description?: string;
  range?: [number, number];
  stage?: string;
  model_override?: boolean;
}

interface RetrievalToolParamEntry {
  default: string | number;
  max?: number;
  description?: string;
}

interface RetrievalConfig {
  env?: Record<string, RetrievalEnvEntry>;
  tool_params?: Record<string, Record<string, RetrievalToolParamEntry>>;
}

// config/retrieval.toml lives at the repo root, two levels up from infra/lib/.
const DEFAULT_TOML_PATH = join(__dirname, '..', '..', 'config', 'retrieval.toml');

let cachedConfig: RetrievalConfig | undefined;

function loadRetrievalConfig(tomlPath: string = DEFAULT_TOML_PATH): RetrievalConfig {
  if (cachedConfig) {
    return cachedConfig;
  }
  const raw = readFileSync(tomlPath, 'utf-8');
  cachedConfig = TOML.parse(raw) as unknown as RetrievalConfig;
  return cachedConfig;
}

/**
 * Extract env var defaults from retrieval.toml as a Record<string, string>
 * (the shape CDK's `environment` prop on lambda.Function expects).
 *
 * @param overrides Values that take precedence over the TOML defaults —
 *   use this for anything resolved from other stacks (table names,
 *   callback URLs, graph IDs) or for context-driven operator overrides.
 */
export function getRetrievalEnv(overrides: Record<string, string> = {}): Record<string, string> {
  const config = loadRetrievalConfig();
  const env: Record<string, string> = {};
  for (const [key, entry] of Object.entries(config.env ?? {})) {
    env[key] = String(entry.default);
  }
  return { ...env, ...overrides };
}

/** Read a single documented env var's default, for one-off use in a stack. */
export function getRetrievalEnvDefault(key: string): string | undefined {
  const config = loadRetrievalConfig();
  const entry = config.env?.[key];
  return entry === undefined ? undefined : String(entry.default);
}

/** Expose tool_params (model-controlled retrieval knobs) for tooling/docs use. */
export function getRetrievalToolParams(): Record<string, Record<string, RetrievalToolParamEntry>> {
  const config = loadRetrievalConfig();
  return config.tool_params ?? {};
}
