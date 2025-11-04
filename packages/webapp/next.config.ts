import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  experimental: {
    esmExternals: 'loose',
  },
  transpilePackages: [
    'flowtoken',
    'remark-gfm',
    'micromark',
    'micromark-core-commonmark',
    'micromark-extension-gfm',
    'micromark-extension-gfm-footnote',
    'decode-named-character-reference',
    'character-entities',
  ],
  webpack(config) {
    config.resolve.extensionAlias = {
      '.js': ['.js', '.mjs', '.cjs'],
    };
    return config;
  },
};

export default nextConfig;
