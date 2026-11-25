# Third-party notices

## Apache ECharts

The interactive chart runtime includes a custom browser bundle of
[Apache ECharts 6.1.0](https://echarts.apache.org/), licensed under the Apache
License 2.0.

- Bundle: `vendor/echarts.custom.min.js`
- Build entry: `scripts/echarts-entry.js`
- Rebuild: `npm ci && npm run build:charts`
- SHA-256: `7811c6c7eec70003148a08d0f5af52744fcf996aad42e1ce4be35141c18069ba`

The upstream license and NOTICE are included as `vendor/echarts.LICENSE.txt` and
`vendor/echarts.NOTICE.txt`. ECharts identifies bundled d3.js-derived code under
the BSD 3-Clause license; that license is included as `vendor/LICENSE-d3.txt`.