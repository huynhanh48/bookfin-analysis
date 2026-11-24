// Add a monochrome OHLC choice in Pro and map it to the existing v9 renderer.
import { readFileSync, writeFileSync } from "node:fs";

const mode = "ohlc_black";
const label = "OHLC màu đen";
const pro = new URL("../node_modules/@klinecharts/pro/dist/klinecharts-pro.js", import.meta.url);
const core = new URL("../node_modules/klinecharts/dist/index.esm.js", import.meta.url);

function replaceOnce(source, needle, replacement) {
  if (source.split(needle).length !== 2) {
    throw new Error("KLineCharts OHLC patch no longer matches the installed version.");
  }
  return source.replace(needle, replacement);
}

let proSource = readFileSync(pro, "utf8");
let coreSource = readFileSync(core, "utf8");
if (!proSource.includes(mode)) {
  const option = '{ key: "ohlc", text: l("ohlc", e) },';
  proSource = replaceOnce(proSource, option, `${option}
        { key: "${mode}", text: "${label}" },`);
  const selected = 'return l(C, e.locale);';
  proSource = replaceOnce(proSource, selected, `return C === "${mode}" ? "${label}" : l(C, e.locale);`);
}
if (!proSource.includes("OHLC: đổi màu")) {
  const restore = 'return [{\n        children: l("restore_default", e.locale),';
  proSource = replaceOnce(proSource, restore, `const candleType = B.formatValue(t(), "candle.type");
      const isOhlc = candleType === "ohlc" || candleType === "${mode}";
      return [...(isOhlc ? [{
        children: "OHLC: đổi màu",
        onClick: () => i(r()[0], candleType === "${mode}" ? "ohlc" : "${mode}")
      }] : []), {
        children: l("restore_default", e.locale),`);
}
if (!coreSource.includes(mode)) {
  const options = 'var candleStyles = chartStore.getStyles().candle;\n        return {';
  coreSource = replaceOnce(coreSource, options, `var candleStyles = chartStore.getStyles().candle;
        if (candleStyles.type === '${mode}') {
            return {
                type: CandleType.Ohlc,
                styles: Object.assign({}, candleStyles.bar, {
                    upColor: '#000000',
                    downColor: '#000000',
                    noChangeColor: '#000000'
                })
            };
        }
        return {`);
}
writeFileSync(pro, proSource);
writeFileSync(core, coreSource);
