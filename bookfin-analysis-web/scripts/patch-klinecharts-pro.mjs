#!/usr/bin/env node
/**
 * Bổ sung `getChart()` vào API của `@klinecharts/pro`.
 *
 * KLineChart Pro 0.1.1 không expose chart instance ra ngoài (chỉ có set/get theme,
 * symbol, period, styles), nên không thể đọc/khôi phục overlay để lưu localStorage.
 * Script này chèn một dòng vào object `ref({...})` của ChartProComponent, nơi biến
 * `n` chính là KLineChart instance.
 *
 * Chạy tự động qua `postinstall`. Không sửa tay: nếu không tìm thấy pattern (do
 * nâng version), script báo lỗi rõ ràng và thoát, app vẫn chạy nhưng không lưu nét vẽ.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = resolve(ROOT, "node_modules/@klinecharts/pro/dist/klinecharts-pro.js");
const MARKER = "getChart: () => n";

let source;
try {
  source = readFileSync(DIST, "utf8");
} catch {
  console.log("[patch-pro] Không tìm thấy @klinecharts/pro, bỏ qua.");
  process.exit(0);
}

if (source.includes(MARKER)) {
  console.log("[patch-pro] Đã patch trước đó.");
  process.exit(0);
}

const NEEDLE = "e.ref({";
const first = source.indexOf(NEEDLE);
if (first < 0 || source.indexOf(NEEDLE, first + 1) >= 0) {
  console.error(
    "[patch-pro] Không xác định được điểm chèn trong klinecharts-pro.js " +
      "(pattern `e.ref({` không duy nhất). Nét vẽ sẽ không được lưu.",
  );
  process.exit(0);
}

writeFileSync(DIST, source.replace(NEEDLE, `${NEEDLE}\n    ${MARKER},`), "utf8");
console.log("[patch-pro] Đã bổ sung getChart() cho chart instance.");
