import * as cdk from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';

/**
 * Optional `costAlerts` CDK context. Every field has a default, so the alarms
 * deploy without it; set `email` to get notified.
 */
export interface CostAlertsConfig {
  /** Address subscribed to the alert topic (confirm the email AWS sends). */
  email?: string;
  /** Questions per hour that trigger the hourly alarm. */
  questionsPerHour?: number;
  /** Questions per day that trigger the daily alarm. */
  questionsPerDay?: number;
  /** Monthly Bedrock budget in USD (actual and forecast alerts at 100%). */
  monthlyBedrockUsd?: number;
}

/**
 * Bedrock billing services the budget tracks. Claude models bill as separate
 * Marketplace services named after the model, so a model swap needs its name
 * added here (Cost Explorer > Service lists the exact names).
 */
const BEDROCK_BILLING_SERVICES = [
  'Amazon Bedrock',
  'Claude Sonnet 4.6 (Amazon Bedrock Edition)',
  'Claude Haiku 4.5 (Amazon Bedrock Edition)',
];

export interface CostAlarmsProps {
  /** The retrieval Lambda: one invocation per question (~$0.27 of Bedrock). */
  retrievalFunction: lambda.IFunction;
  config?: CostAlertsConfig;
}

/**
 * Early warning for runaway Bedrock spend ("denial of wallet").
 *
 * Alarms on retrieval Lambda invocations rather than the AWS/Bedrock token
 * metrics, which undercount badly in this account (an Oct 12 window with
 * hundreds of Sonnet calls recorded 10). Defaults sit well above today's peak
 * (16 questions/hour) with room for the 112-staff rollout. The budget catches
 * spend from any source, including ingestion and harness runs.
 */
export class CostAlarms extends Construct {
  readonly topic: sns.Topic;

  constructor(scope: Construct, id: string, props: CostAlarmsProps) {
    super(scope, id);
    const cfg = props.config ?? {};
    const perHour = cfg.questionsPerHour ?? 120;
    const perDay = cfg.questionsPerDay ?? 600;
    const budgetUsd = cfg.monthlyBedrockUsd ?? 500;

    this.topic = new sns.Topic(this, 'CostAlertTopic', {
      displayName: 'Wisconsin DOR chatbot cost alerts',
    });
    this.topic.addToResourcePolicy(
      new iam.PolicyStatement({
        actions: ['sns:Publish'],
        principals: [new iam.ServicePrincipal('budgets.amazonaws.com')],
        resources: [this.topic.topicArn],
        conditions: { StringEquals: { 'aws:SourceAccount': cdk.Stack.of(this).account } },
      })
    );
    if (cfg.email) {
      this.topic.addSubscription(new subs.EmailSubscription(cfg.email));
    }
    const notify = new cwActions.SnsAction(this.topic);

    const questions = (period: cdk.Duration) =>
      props.retrievalFunction.metricInvocations({ period, statistic: 'Sum' });

    const hourly = new cloudwatch.Alarm(this, 'QuestionsPerHourAlarm', {
      alarmDescription: `More than ${perHour} chatbot questions in an hour (~$0.27 of Bedrock each).`,
      metric: questions(cdk.Duration.hours(1)),
      threshold: perHour,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    const daily = new cloudwatch.Alarm(this, 'QuestionsPerDayAlarm', {
      alarmDescription: `More than ${perDay} chatbot questions in a day (~$0.27 of Bedrock each).`,
      metric: questions(cdk.Duration.days(1)),
      threshold: perDay,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    hourly.addAlarmAction(notify);
    daily.addAlarmAction(notify);

    const subscriber = { subscriptionType: 'SNS', address: this.topic.topicArn };
    new budgets.CfnBudget(this, 'BedrockMonthlyBudget', {
      budget: {
        budgetName: `${cdk.Stack.of(this).stackName}-bedrock-monthly`,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: budgetUsd, unit: 'USD' },
        costFilters: { Service: BEDROCK_BILLING_SERVICES },
      },
      notificationsWithSubscribers: [
        {
          notification: {
            notificationType: 'FORECASTED',
            comparisonOperator: 'GREATER_THAN',
            threshold: 100,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [subscriber],
        },
        {
          notification: {
            notificationType: 'ACTUAL',
            comparisonOperator: 'GREATER_THAN',
            threshold: 100,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [subscriber],
        },
      ],
    });
  }
}
