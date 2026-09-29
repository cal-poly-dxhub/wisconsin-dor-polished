/** @bun */
import { describe, expect, test } from 'bun:test';
import { renderToString } from 'react-dom/server';
import { ProfileMenu } from '../profile-menu';

const noop = () => {};

describe('ProfileMenu', () => {
  test('shows the name, the email and every menu action', () => {
    const html = renderToString(
      <ProfileMenu email="jane.doe@wisconsin.gov" onOpenSettings={noop} onSignOut={noop} />,
    );
    expect(html).toContain('jane.doe');
    expect(html).toContain('jane.doe@wisconsin.gov');
    expect(html).toContain('Settings');
    expect(html).toContain('Sign out');
    for (const label of ['Light', 'Dark', 'System']) {
      expect(html).toContain(`aria-label="${label}"`);
    }
    expect(html).not.toContain('Admin<');
  });

  test('marks admins', () => {
    const html = renderToString(
      <ProfileMenu email="a@b.gov" isAdmin onOpenSettings={noop} onSignOut={noop} />,
    );
    expect(html).toContain('Admin<');
  });
});
