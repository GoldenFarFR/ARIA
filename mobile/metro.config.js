// Metro configuration -- https://docs.expo.dev/guides/customizing-metro/
//
// 07/08 -- fixes an EAS "Bundle JavaScript" failure: `jose` (pulled in by
// @privy-io/expo -> @privy-io/js-sdk-core) ships separate node and browser
// builds and declares both in its package exports. Metro was resolving the
// "import" condition, landing on dist/node/esm, which does `import
// { Buffer } from 'buffer'` -- a Node built-in that simply does not exist in
// React Native, so the bundle failed with "Unable to resolve module buffer".
//
// Asking for the "browser" condition FIRST hands us jose's own dependency-free
// build, which is what a React Native runtime actually needs. Preferred over
// polyfilling `buffer` and aliasing it in extraNodeModules: that would ship an
// extra shim to work around a build we should not have been using in the first
// place, and would leave every other node-only import in that build (crypto,
// util...) waiting to break the same way later.
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

config.resolver.unstable_enablePackageExports = true;
// Order matters -- Metro takes the first condition a package declares.
config.resolver.unstable_conditionNames = ['browser', 'react-native', 'require', 'import'];

module.exports = config;
