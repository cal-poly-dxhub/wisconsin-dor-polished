import type { OpenNextConfig } from '@opennextjs/aws/types/open-next.js';

const config: OpenNextConfig = {
  default: {
    override: {
      wrapper: 'express-dev',
      converter: 'node',
      incrementalCache: 'fs-dev',
      queue: 'direct',
      tagCache: 'fs-dev',
    },
  },
  // imageOptimization: {
  //   override: {
  //     wrapper: 'dummy', // do NOT use aws-lambda here for local dev
  //     converter: 'dummy',
  //   },
  //   loader: 'fs-dev',
  // },
};

export default config;
