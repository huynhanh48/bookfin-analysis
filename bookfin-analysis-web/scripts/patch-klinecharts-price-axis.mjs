// KLineChart 9 has a tick hook but no formatter for the last-price and
// crosshair labels. Format all three at the drawing boundary in raw VND.
import { readFileSync, writeFileSync } from "node:fs";

const file = new URL("../node_modules/klinecharts/dist/index.esm.js", import.meta.url);
const source = readFileSync(file, "utf8");
if (!source.includes("function bookfinFormatPriceAxis(")) {
  const formatter = `function bookfinFormatPriceAxis(value) {
    var amount = Number(value) / 1000;
    var rounded = amount.toFixed(2);
    var fraction = rounded.split(".")[1];
    return (fraction === "00" ? rounded : rounded.replace(/0$/, "")).replace(".", ",");
}
`;
  function replaceOnce(source, needle, replacement) {
    if (source.split(needle).length !== 2) {
      throw new Error("KLineChart price-axis patch no longer matches the installed version.");
    }
    return source.replace(needle, replacement);
  }

  let patched = replaceOnce(source, "function formatPrecision(value, precision) {",
    formatter + "function formatPrecision(value, precision) {");
  patched = replaceOnce(patched,
    "text = formatPrecision(close_1, precision.price);",
    "text = bookfinFormatPriceAxis(close_1);");
  patched = replaceOnce(patched,
    "precision_1 = chartStore.getPrecision().price;",
    "return bookfinFormatPriceAxis(value);");
  patched = replaceOnce(patched,
    "optimalTicks.push({ text: v, coord: y, value: value });",
    "optimalTicks.push({ text: _this.isInCandle() && type !== YAxisType.Percentage ? bookfinFormatPriceAxis(value) : v, coord: y, value: value });");
  writeFileSync(file, patched);
}
