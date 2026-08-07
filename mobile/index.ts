// Web Crypto polyfills (fast-text-encoding, @ethersproject/shims, then
// react-native-get-random-values, in that order) -- required by Privy's SDK
// chain (@privy-io/expo -> js-sdk-core), which expects a global `crypto`
// object. Hermes has no such global: without these, PrivyProvider throws
// "ReferenceError: Property 'crypto' doesn't exist" on the very first
// render, crashing the app before anything is shown. Must be imported
// before every other import, in this file only (the app's true entry point).
import 'fast-text-encoding';
import '@ethersproject/shims';
import 'react-native-get-random-values';

import { registerRootComponent } from 'expo';

import App from './App';

// registerRootComponent calls AppRegistry.registerComponent('main', () => App);
// It also ensures that whether you load the app in Expo Go or in a native build,
// the environment is set up appropriately
registerRootComponent(App);
