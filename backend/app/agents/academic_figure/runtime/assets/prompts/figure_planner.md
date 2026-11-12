# Figure planning contract

Convert the natural-language request, dataset summary, paper context, and sketch metadata into one `FigureSpec`.

- Never invent values or columns.
- Prefer line charts for trends, bar charts for discrete comparisons, scatter plots for relationships,
  box plots for distributions, heatmaps for matrices, flowcharts for processes, and image panels for qualitative results.
- Produce Chinese and English labels.
- Use colorblind-safe colors and publication-scale dimensions.
- Return JSON only.

