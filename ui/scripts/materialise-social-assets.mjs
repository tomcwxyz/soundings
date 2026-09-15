import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceParts = [
  "src/assets/social/soundings-og.part0.b64",
  "src/assets/social/soundings-og.part1.b64",
  "src/assets/social/soundings-og.part2a.b64",
  "src/assets/social/soundings-og.part2b.b64",
  "src/assets/social/soundings-og.part3a.b64",
  "src/assets/social/soundings-og.part3b.b64",
];

const base64Parts = await Promise.all(
  sourceParts.map((part) => readFile(resolve(root, part), "utf8")),
);
const image = Buffer.from(base64Parts.map((part) => part.trim()).join(""), "base64");

const expectedBytes = 38_659;
const expectedSha256 = "934787994a01a403e427bda8e132afcd8ac098d4b8152ecc8c91f5d5569a5702";
const sha256 = createHash("sha256").update(image).digest("hex");

if (image.byteLength !== expectedBytes || sha256 !== expectedSha256) {
  throw new Error(
    `Social image integrity check failed: got ${image.byteLength} bytes / ${sha256}`,
  );
}

const output = resolve(root, "public/social/soundings-og.jpg");
await mkdir(dirname(output), { recursive: true });
await writeFile(output, image);

console.log(`Materialised ${output} (${image.byteLength} bytes)`);
