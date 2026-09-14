/**
 * Electron's `original-fs`: Node's `fs` WITHOUT the asar patching Electron
 * applies to the regular module (which makes every `*.asar` look like a
 * directory). No `@types` package ships it, so declare it here — it has the
 * exact surface of `node:fs`. Only ever imported inside packaged Electron
 * (src/main.ts, the macOS update installer); plain Node has no such module.
 */
declare module "node:original-fs" {
  export * from "node:fs";
}
