import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';

/**
 * Set retention on the log group CDK already manages for a function.
 *
 * With `@aws-cdk/aws-lambda:useCdkManagedLogGroup` (infra/cdk.json) every
 * Function gets a `LogGroup` child at a 731-day default. The `logRetention`
 * prop would orphan that group out of the stack and add a separate
 * custom-resource Lambda instead, so this edits the managed group in place:
 * one property change in the template, no resources added or removed.
 */
export function setLogRetention(fn: lambda.Function, days: logs.RetentionDays): void {
  const child = fn.node.tryFindChild('LogGroup');
  const cfn = (
    child instanceof logs.CfnLogGroup ? child : child?.node.defaultChild
  ) as logs.CfnLogGroup | undefined;
  if (!(cfn instanceof logs.CfnLogGroup)) {
    throw new Error(
      `${fn.node.path} has no CDK-managed log group; is useCdkManagedLogGroup still on?`
    );
  }
  cfn.retentionInDays = days;
}
