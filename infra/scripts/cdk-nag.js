#!/usr/bin/env node
/**
 * Run the cdk-nag AWS Solutions rules over the synthesized templates.
 *
 *   bun run nag        (from infra/: synth, then this script)
 *
 * Wiring cdk-nag into bin/wisconsin-app.ts via Validations produced an empty
 * report under the CDK CLI, so this loads each template in cdk_out_dir/ into a
 * scratch app with CfnInclude and validates those L1 resources instead; the
 * rules only inspect CloudFormation properties, so the coverage is the same.
 *
 * ACCEPTED lists the rules reviewed and accepted, with the reason. The script
 * exits 1 when any other rule fires, so a new finding cannot slip in quietly.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const cdk = require('aws-cdk-lib');
const include = require('aws-cdk-lib/cloudformation-include');
const { AwsSolutionsChecks } = require('cdk-nag');

const ACCEPTED = {
  'AwsSolutions-IAM4': 'AWSLambdaBasicExecutionRole on Lambda roles (CDK default; logs only).',
  'AwsSolutions-IAM5':
    'Wildcards from CDK grant* helpers (table index/*, bucket/*), Bedrock model-family ARNs, ' +
    'and third-party constructs (cdk-nextjs-standalone, generative-ai-cdk-constructs).',
  'AwsSolutions-APIG4':
    'WebSocket $connect verifies the Cognito ID token in its Lambda; $disconnect cannot take an authorizer.',
  'AwsSolutions-DDB3':
    'Tables without PITR are rebuildable (FAQ URL map, model config from git) or ephemeral (rate-limit counters).',
  'AwsSolutions-ECS2': 'Ingestion task env vars are non-secret config (bucket names, graph id).',
  'AwsSolutions-SQS3': 'cdk-nextjs-standalone revalidation queue.',
  'AwsSolutions-CFR1': 'No geo restriction wanted.',
  'AwsSolutions-CFR4': 'Default CloudFront certificate; its TLS policy is fixed without a custom domain.',
  'AwsSolutions-CFR7': 'cdk-nextjs-standalone S3 origin.',
  // Open items tracked in docs/tasks.md (security follow-ups), not yet decided:
  'AwsSolutions-L1': 'TODO: Lambda runtime upgrade (tasks.md).',
  'AwsSolutions-APIG1': 'TODO: API access logging (tasks.md).',
  'AwsSolutions-S1': 'TODO: S3 server access logs (tasks.md).',
  'AwsSolutions-CFR3': 'TODO: CloudFront access logs (tasks.md).',
  'AwsSolutions-CFR2': 'TODO: WAF decision (tasks.md).',
  'AwsSolutions-VPC7': 'TODO: ingestion VPC flow logs (tasks.md).',
  'AwsSolutions-ECS4': 'TODO: Container Insights (tasks.md).',
  'AwsSolutions-COG2': 'TODO: admin MFA decision (tasks.md).',
  'AwsSolutions-COG8': 'TODO: Cognito plus tier, needed for some MFA/threat-protection options (tasks.md).',
};

const assembly = path.join(__dirname, '..', 'cdk_out_dir');
// Only templates the current synth produced: each stack's root template plus
// the nested-stack templates its assets.json references (cdk_out_dir keeps
// stale files from earlier synths and removed nested stacks).
const manifest = JSON.parse(fs.readFileSync(path.join(assembly, 'manifest.json'), 'utf8'));
const templates = new Set();
for (const artifact of Object.values(manifest.artifacts ?? {})) {
  if (artifact.type !== 'aws:cloudformation:stack') continue;
  templates.add(artifact.properties.templateFile);
  const assetsFile = (artifact.dependencies ?? []).find(d => d.endsWith('.assets'));
  if (!assetsFile) continue;
  const assets = JSON.parse(fs.readFileSync(path.join(assembly, `${assetsFile}.json`), 'utf8'));
  for (const file of Object.values(assets.files ?? {})) {
    if (file.source.path.endsWith('.nested.template.json')) templates.add(file.source.path);
  }
}
if (!templates.size) {
  console.error(`No templates in ${assembly}; run cdk synth first.`);
  process.exit(2);
}

const outdir = fs.mkdtempSync(path.join(os.tmpdir(), 'cdk-nag-'));
const app = new cdk.App({ outdir });
for (const file of templates) {
  const id = file.replace(/\.(nested\.)?template\.json$/, '').replace(/[^A-Za-z0-9-]/g, '');
  const stack = new cdk.Stack(app, id.slice(0, 120), {
    synthesizer: new cdk.BootstraplessSynthesizer(),
  });
  new include.CfnInclude(stack, 'T', { templateFile: path.join(assembly, file) });
}
cdk.Validations.of(app).addPlugins(new AwsSolutionsChecks(app));
try {
  app.synth();
} catch {
  // Violations make synth throw; the report below is the result.
}

const report = JSON.parse(fs.readFileSync(path.join(outdir, 'validation-report.json'), 'utf8'));
const counts = new Map();
for (const plugin of report.pluginReports) {
  if (plugin.pluginName !== 'AwsSolutions') continue;
  for (const v of plugin.violations) {
    const rule = v.ruleName.split('[')[0];
    const entry = counts.get(rule) ?? { n: 0, description: v.description };
    entry.n += v.violatingConstructs.length;
    counts.set(rule, entry);
  }
}
const unaccepted = [...counts].filter(([rule]) => !ACCEPTED[rule]);
for (const [rule, { n, description }] of [...counts].sort()) {
  const status = ACCEPTED[rule] ? `accepted: ${ACCEPTED[rule]}` : 'NEW';
  console.log(`${rule.padEnd(20)} x${String(n).padEnd(4)} ${description}\n${' '.repeat(26)}${status}`);
}
console.log(`\n${counts.size} rules fired, ${unaccepted.length} not accepted.`);
process.exit(unaccepted.length ? 1 : 0);
