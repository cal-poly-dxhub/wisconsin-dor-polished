import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';

/** Name checked by chat_api `require_admin()` and the frontend `hasAdminGroup`. */
export const ADMIN_GROUP_NAME = 'Admins';

/**
 * Ensures the `Admins` Cognito group exists, so the admin gate is defined in
 * code rather than by a console click (security pass F4).
 *
 * The production group predates this construct and was made by hand. `cdk
 * import` cannot adopt it because the pool lives in a nested stack, so this is
 * an idempotent CreateGroup: it creates the group in a fresh account (DOR's)
 * and is a no-op where it already exists (GroupExistsException is ignored).
 * Nothing is done on delete, so tearing the stack down never removes the group
 * or its members. Membership stays a console / CLI action:
 *   aws cognito-idp admin-add-user-to-group --user-pool-id <id> \
 *     --username <user> --group-name Admins
 */
export class AdminsGroup extends Construct {
  constructor(scope: Construct, id: string, props: { userPool: cognito.IUserPool }) {
    super(scope, id);

    const call: cr.AwsSdkCall = {
      service: 'CognitoIdentityServiceProvider',
      action: 'createGroup',
      parameters: {
        UserPoolId: props.userPool.userPoolId,
        GroupName: ADMIN_GROUP_NAME,
        Description: 'Access to /admin/* (activity, chunks, canvas, ingest).',
      },
      physicalResourceId: cr.PhysicalResourceId.of(`${ADMIN_GROUP_NAME}-group`),
      ignoreErrorCodesMatching: 'GroupExistsException',
    };

    new cr.AwsCustomResource(this, 'Ensure', {
      onCreate: call,
      onUpdate: call,
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['cognito-idp:CreateGroup'],
          resources: [props.userPool.userPoolArn],
        }),
      ]),
      installLatestAwsSdk: false,
    });
  }
}
