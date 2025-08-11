import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import prettierPlugin from 'eslint-plugin-prettier';
import pluginJest from 'eslint-plugin-jest';
import globals from 'globals';

export default tseslint.config(
  // Global ignores
  {
    ignores: [
      '**/.venv/**',
      '**/node_modules/**',
      '**/dist/**',
      '**/build/**',
      '**/.next/**',
      '**/out/**',
      '**/.sst/**',
      '**/sst/**',
      '**/cdk.out/**',
      '**/infra/**/*.js',
      '**/infra/**/*.d.ts',
      '**/sessions/**/*.js',
      '**/sessions/**/*.d.ts',
    ],
  },

  // Global plugins
  {
    plugins: {
      prettier: prettierPlugin,
      jest: pluginJest,
    },
  },

  // Base JavaScript config
  js.configs.recommended,

  // TypeScript configs with type checking for source files
  {
    files: ['**/*.ts', '**/*.tsx'],
    ignores: ['**/*.config.ts', '**/*.d.ts', '**/*.test.ts', '**/*.spec.ts'],
    extends: [
      ...tseslint.configs.recommendedTypeChecked,
      ...tseslint.configs.stylisticTypeChecked,
    ],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: process.cwd(),
      },
      globals: {
        ...globals.browser,
        ...globals.node,
        ...globals.es2021,
      },
    },
    rules: {
      'prettier/prettier': 'error',
    },
  },

  // Config files (no type checking)
  {
    files: ['**/*.config.ts', '**/*.config.js'],
    extends: [...tseslint.configs.recommended],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
    rules: {
      'prettier/prettier': 'error',
      '@typescript-eslint/triple-slash-reference': 'off',
    },
  },

  // Test files
  {
    files: ['**/*.test.ts', '**/*.spec.ts', '**/*.test.js', '**/*.spec.js'],
    languageOptions: {
      globals: {
        ...globals.jest,
      },
    },
    rules: {
      ...pluginJest.configs.recommended.rules,
      'prettier/prettier': 'error',
    },
  }
);
