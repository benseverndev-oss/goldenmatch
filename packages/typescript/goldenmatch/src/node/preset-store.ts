/**
 * preset-store.ts -- named config presets on disk.
 *
 * Port of Python `goldenmatch/prefs/store.py::PresetStore`, which backs the
 * `goldenmatch config {save,load,list,delete,show}` CLI sub-app. A preset is
 * just a config YAML copied to `~/.goldenmatch/presets/<name>.yaml`, so a
 * preset saved by either toolkit is readable by the other.
 *
 * Node-only (filesystem); nothing here belongs in the edge-safe core.
 */

import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { basename, extname, join, resolve } from "node:path";
import { homedir } from "node:os";

export class PresetStore {
  private readonly baseDirPath: string;

  constructor(baseDir?: string) {
    this.baseDirPath = baseDir ?? join(homedir(), ".goldenmatch", "presets");
  }

  get baseDir(): string {
    return this.baseDirPath;
  }

  private pathFor(name: string): string {
    return join(this.baseDirPath, `${name}.yaml`);
  }

  /** Copy a config file in as `<name>.yaml`. Throws if the source is missing. */
  save(name: string, configPath: string): string {
    if (!existsSync(configPath)) {
      throw new Error(`Config file not found: ${configPath}`);
    }
    mkdirSync(this.baseDirPath, { recursive: true });
    const dest = this.pathFor(name);
    copyFileSync(resolve(configPath), dest);
    return dest;
  }

  /** Copy a saved preset out to `dest`. Throws if the preset is missing. */
  load(name: string, dest: string): string {
    const preset = this.pathFor(name);
    if (!existsSync(preset)) {
      throw new Error(`Preset not found: ${name}`);
    }
    copyFileSync(preset, resolve(dest));
    return resolve(dest);
  }

  /** Preset names (file stems), sorted -- mirrors Python's `sorted(glob)`. */
  listPresets(): string[] {
    if (!existsSync(this.baseDirPath)) return [];
    let entries: string[];
    try {
      entries = readdirSync(this.baseDirPath);
    } catch {
      return [];
    }
    return entries
      .filter((e) => extname(e).toLowerCase() === ".yaml")
      .map((e) => basename(e, extname(e)))
      .sort();
  }

  /** Delete a preset. Throws if it does not exist. */
  delete(name: string): void {
    const preset = this.pathFor(name);
    if (!existsSync(preset)) {
      throw new Error(`Preset not found: ${name}`);
    }
    rmSync(preset);
  }

  /** Raw YAML text of a preset (for `config show`). Throws if missing. */
  show(name: string): string {
    const preset = this.pathFor(name);
    if (!existsSync(preset)) {
      throw new Error(`Preset not found: ${name}`);
    }
    return readFileSync(preset, "utf-8");
  }
}
