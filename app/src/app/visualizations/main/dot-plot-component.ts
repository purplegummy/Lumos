// libraries
import * as d3 from "d3";
import $ from "jquery";
import * as seedrandom from "seedrandom";
// local
import { DotPlotConfig } from "src/app/models/viz";
import { sequentialColorRange, SessionPage } from "src/app/models/config";
import { UtilsService } from "src/app/services/utils.service";
import { ChatService } from "src/app/services/socket.service";
import { cleanAttr, compareCategoryValues } from "src/app/models/attribute-labels";

export class DotPlot {
  dotPlotConfig;
  plotWidth: number;
  plotHeight: number;
  plotGroup;

  constructor(
    public utilsService: UtilsService,
    public chatService: ChatService,
    public global: SessionPage,
    public userConfig,
    public appConfig
  ) {
    this.dotPlotConfig = new DotPlotConfig();
  }

  /**
   * Create variables needed to draw and update plot.
   */
  initialize() {
    let context = this;
    const container = "#plot_container";
    const width = $(container).parent().width();
    const height = $(container).parent().height();
    const plotMargins = { top: 50, bottom: 50, left: 60, right: 30 };

    context.plotWidth = width - plotMargins.left - plotMargins.right;
    context.plotHeight = height - plotMargins.top - plotMargins.bottom;

    $(container).empty();

    // Add containing SVG
    let svg = d3.select(container).append("svg").attr("width", width).attr("height", height);

    // Add linear gradient to SVG definition for use in color scale FIRST
    let grad = svg
      .append("defs")
      .append("linearGradient")
      .attr("id", "grad")
      .attr("x1", "0%")
      .attr("x2", "100%")
      .attr("y1", "0%")
      .attr("y2", "0%");
    grad
      .selectAll("stop")
      .data(sequentialColorRange)
      .enter()
      .append("stop")
      .style("stop-color", (d) => d.toString())
      .attr("offset", (_, i) => 100 * (i / (sequentialColorRange.length - 1)) + "%");

    // Add plot group
    context.plotGroup = svg
      .append("g")
      .classed("plot", true)
      .attr("transform", `translate(${plotMargins.left}, ${plotMargins.top})`);

    // Add X and Y axis groups
    context.dotPlotConfig.yAxisGroup = context.plotGroup.append("g").classed("y", true).classed("axis", true);
    context.dotPlotConfig.xAxisGroup = context.plotGroup
      .append("g")
      .classed("x", true)
      .classed("axis", true)
      .attr("transform", `translate(${0}, ${context.plotHeight})`);

    // Add dots groups
    context.dotPlotConfig.dotsGroup = context.plotGroup.append("g").classed("dots", true);

    // Add legend group, text and gradient rectangle
    context.dotPlotConfig.legendGroup = context.plotGroup.append("g").classed("legend", true);
    if (true) {
      let xPos = context.plotWidth; // x position of element, gets updated dynamically
      const pad = 5; // padding between elements
      const gradRectWidth = context.plotWidth / 5; // width of gradient rectangle
      const el = context.dotPlotConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text("More Focus");
      xPos -= Math.abs(el.node().getBBox()["x"]) + gradRectWidth + pad;
      context.dotPlotConfig.legendGroup
        .append("rect")
        .attr("transform", `translate(${xPos}, ${(-3 / 4) * plotMargins.top})`)
        .attr("width", gradRectWidth)
        .attr("height", (1 / 8) * plotMargins.top)
        .style("rx", "4")
        .style("fill", "url(#grad)");
      xPos -= pad;
      context.dotPlotConfig.legendGroup
        .append("text")
        .attr("transform", `translate(${xPos}, ${(-5 / 8) * plotMargins.top})`)
        .attr("text-anchor", "end")
        .text("Less Focus");
    }

    // Create unsupported text to display if chart cannot render
    context.dotPlotConfig.unsupportedMessage = `
      <tspan>
        If using numerical
        (<tspan style="font-family: 'Font Awesome 5 Free'; font-weight: 800 !important">&#xf292;</tspan>)
        attributes,
      </tspan>
      <tspan x="0" dy="1.2em">
        you must have
        <tspan style="font-weight: 800 !important">more than one</tspan>!
      </tspan>`;
  }

  /**
   * Calculate new values and re-draw plot.
   */
  update() {
    let context = this;
    let originalDatasetDict = context.userConfig["originalDatasetDict"];
    let dataset = context.appConfig[context.global.appMode];
    const PK = dataset["primaryKey"];

    // if there's no dataset don't update the chart
    if (!originalDatasetDict) return;

    // Clear unsupported message
    context.dotPlotConfig.dotsGroup.select(".unsupported-text").remove();

    // create raw data object
    let rawData = Object.keys(originalDatasetDict).map((id) => {
      return {
        ...originalDatasetDict[id],
        xVar: dataset["xVar"] == null ? null : originalDatasetDict[id][dataset["xVar"]],
        yVar: dataset["yVar"] == null ? null : originalDatasetDict[id][dataset["yVar"]],
      };
    });

    // filter raw data into a prepared data set
    let prepared = rawData;
    ["N", "O"].forEach((dataType) =>
      dataset.attributeDatatypeList[dataType].forEach((attr) => {
        let filterModel = dataset["attributes"][attr]["filterModel"];
        prepared = prepared.filter((item) => {
          return filterModel.indexOf(item[attr]) !== -1;
        });
      })
    );
    ["T", "Q"].forEach((dataType) =>
      dataset.attributeDatatypeList[dataType].forEach((attr) => {
        let filterModel = dataset["attributes"][attr]["filterModel"];
        prepared = prepared.filter((item) => {
          return (
            parseFloat(item[attr]) >= parseFloat(filterModel[0]) && parseFloat(item[attr]) <= parseFloat(filterModel[1])
          );
        });
      })
    );

    // Create scales and axes based on vis matrix
    let xIsNA = dataset["xVar"] == null;
    let yIsNA = dataset["yVar"] == null;
    let xIsQ = context.utilsService.isMeasure(dataset, dataset["xVar"], "Q");
    let yIsQ = context.utilsService.isMeasure(dataset, dataset["yVar"], "Q");

    // initialize x and y scales
    context.dotPlotConfig.xScale = d3.scaleBand().rangeRound([0, context.plotWidth]).padding(0.1);
    context.dotPlotConfig.yScale = d3.scaleBand().rangeRound([context.plotHeight, 0]).padding(0.1);

    if (xIsQ || yIsQ || (xIsNA && yIsNA)) {
      // [Q x any] OR [any x Q] or [NA x NA] => unsupported
      prepared = []; // ensures no points are drawn
      context.dotPlotConfig.xAxisGroup.selectAll("*").remove();
      context.dotPlotConfig.yAxisGroup.selectAll("*").remove();
      context.dotPlotConfig.legendGroup.style("display", "none");
      context.dotPlotConfig.dotsGroup
        .append("text")
        .attr("class", "unsupported-text")
        .attr("transform", `translate(${context.plotWidth / 2},${context.plotHeight / 2})`)
        .attr("text-anchor", "middle")
        .html(context.dotPlotConfig.unsupportedMessage);
    } else {
      // [N/O/T/NA x N/O/T/NA] => supported
      context.dotPlotConfig.legendGroup.style("display", "block");
      if (!xIsNA) {
        // x is N/O/T => horizontal dots
        context.dotPlotConfig.xScale.domain(
          rawData
            .map(function (d) {
              return d["xVar"];
            })
            .sort(function (x, y) {
              return compareCategoryValues(dataset["xVar"], x, y); // sort domain
            })
        );
        context.dotPlotConfig.xAxis = d3.axisBottom(context.dotPlotConfig.xScale);
        context.dotPlotConfig.xAxisGroup.call(context.dotPlotConfig.xAxis);
        context.dotPlotConfig.xAxisGroup.select(".x.axis.title").remove();
        context.dotPlotConfig.xAxisGroup
          .append("g")
          .classed("x axis title", true)
          .attr("opacity", 1)
          .attr("transform", `translate(${context.plotWidth / 2}, 0)`)
          .append("text")
          .attr("text-anchor", "middle")
          .attr("fill", "currentColor")
          .attr("dy", "3.71em")
          .text(cleanAttr(dataset["xVar"]));
      } else {
        // x is NA => remove x axis (if present)
        context.dotPlotConfig.xAxisGroup.selectAll("*").remove();
      }
      if (!yIsNA) {
        // y is N/O/T => vertical dots
        context.dotPlotConfig.yScale.domain(
          rawData
            .map(function (d) {
              return d["yVar"];
            })
            .sort(function (x, y) {
              return compareCategoryValues(dataset["yVar"], x, y); // sort domain
            })
        );
        context.dotPlotConfig.yAxis = d3.axisLeft(context.dotPlotConfig.yScale);
        context.dotPlotConfig.yAxisGroup.call(context.dotPlotConfig.yAxis);
        context.dotPlotConfig.yAxisGroup.select(".y.axis.title").remove();
        context.dotPlotConfig.yAxisGroup
          .append("g")
          .classed("y axis title", true)
          .attr("opacity", 1)
          .attr("transform", `translate(-30, ${context.plotHeight / 2})`)
          .append("text")
          .attr("fill", "currentColor")
          .text(cleanAttr(dataset["yVar"]));
        context.dotPlotConfig.yAxisGroup
          .selectAll("text")
          .style("text-anchor", "middle")
          .attr("dx", "0.8em")
          .attr("dy", "-1.21em")
          .attr("transform", "rotate(-90)");
      } else {
        // y is NA => remove y axis (if present)
        context.dotPlotConfig.yAxisGroup.selectAll("*").remove();
      }
    }

    // stagger every other tick label
    context.dotPlotConfig.xAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("y2", 15);
        d3.select(this).select("text").attr("dy", "1.91em");
      }
    });
    context.dotPlotConfig.yAxisGroup.selectAll(".tick").each(function (_, i) {
      if (i % 2 !== 0) {
        d3.select(this).select("line").attr("x2", -15);
        d3.select(this).select("text").attr("dy", "-2.41em");
      }
    });

    // JOIN one circle per TEEN (individual records), NOT per cell. This is the
    // same per-datum binding the scatter plot uses, keyed on the primary key, so
    // a hover or click touches exactly one teen. The old code aggregated each
    // (xCat,yCat) cell into a single bubble (d3.groups) and fired group hover /
    // group click, attributing attention (and selection) to EVERY teen in the
    // cell at once -- that aggregation and its group interactions are gone.
    let dataBound = context.dotPlotConfig.dotsGroup.selectAll(".post").data(prepared, (d) => d[PK]);

    // DELETE extra points
    dataBound.exit().remove();

    // ENTER new points
    let enterSelection = dataBound.enter().append("g").classed("post", true);

    // UPDATE all existing points
    enterSelection.append("circle");
    enterSelection
      .merge(dataBound)
      .select("circle")
      // base = cell center (same formula the old bubble used), + independent
      // per-axis jitter so co-located teens declump instead of stacking.
      .attr("transform", (d) => dotJitterTranslate(d, context, xIsNA, yIsNA))
      // match the scatter dot radius (scatter-plot-component.ts:326)
      .attr("r", 6)
      .style("fill", (d) => {
        // per-teen focus color, exactly as the scatter plot colors its dots
        // (scatter-plot-component.ts:327-333): white in CONTROL, else the
        // interaction/focus color written onto the dict object by reference.
        if (context.global.appLayout === "CONTROL") return "white";
        let dataPoint = originalDatasetDict[d[PK]];
        context.utilsService.colorDataPoint(context, dataPoint, prepared);
        return dataPoint["color"];
      })
      .style("fill-opacity", 0.8)
      // per-teen selected styling (only the selected teen's own point highlights)
      .style("stroke-width", (d) => (d["selected"] ? "3px" : "1px"))
      .style("stroke", (d) => (d["selected"] ? "brown" : "black"))
      .style("cursor", "pointer")
      .on("click", function (event, d) {
        // single-teen add/remove, matching scatter-plot-component.ts:338-360
        const isControl = context.global.appLayout === "CONTROL";
        const isAdmin = context.global.appType === "ADMIN";
        if (isControl) {
          const selectedCount = Object.keys(dataset["selectedObjects"]).length;
          if (d["selected"]) {
            context.utilsService.clickRemoveItem(context, event, d);
          } else if (selectedCount < 10) {
            context.utilsService.clickAddItem(context, event, d);
          }
        } else if (isAdmin) {
          d["selected"]
            ? context.utilsService.clickRemoveItem(context, event, d)
            : context.utilsService.clickAddItem(context, event, d);
        }
        // repaint the selection stroke locally (the server round trip is a no-op
        // in tutorial mode), harmless if a real redraw also follows.
        d3.select(this)
          .style("stroke-width", d["selected"] ? "3px" : "1px")
          .style("stroke", d["selected"] ? "brown" : "black");
      })
      .on("mouseover", function (event, d) {
        // single-teen hover, matching scatter-plot-component.ts:361-368. This
        // routes to the singular `hoveredObject` model (mouseoverItem), so the
        // dot plot now uses the scatter's single-item Details panel.
        if (context.global.appLayout === "CONTROL") {
          d3.select(this).style("fill", "#AED6F1");
          context.utilsService.mouseoverItem(context, event, d);
        } else {
          context.utilsService.mouseoverItem(context, event, d, this, "fill");
        }
      })
      .on("mouseout", function (event, d) {
        if (context.global.appLayout === "CONTROL") {
          d3.select(this).style("fill", "white");
        }
        context.utilsService.mouseoutItem(context, event, d);
      });
  }
}

// Both dot-plot axes are categorical (N/O/T) or NA -- never quantitative (Q x any
// routes to the unsupported branch). Mirror scatter-plot-component.ts's CAT_JITTER
// so the declump magnitude matches the scatter's categorical jitter. (That const
// is module-private over there, so the value is repeated here, not imported.)
const CAT_JITTER = 0.3;

/**
 * Per-teen translate(x,y) for the dot plot. Each teen's base is its cell center
 * -- the SAME formula the old aggregate bubble used (align left/bottom when an
 * axis is NA) -- plus an INDEPENDENT random jitter on BOTH axes. Unlike the
 * scatter's mixed Q x categorical path (one axis jittered at a time), both axes
 * here are categorical, so both get jitter. The RNG is seeded per teen
 * (primaryKey + participantId) -- the exact seed scatter-plot-component.ts:400
 * uses -- so a teen's point stays put across redraws. When an axis is NA it has
 * no bandwidth of its own, so the other axis's bandwidth sets the spread.
 */
function dotJitterTranslate(d, context, xIsNA, yIsNA) {
  const dataset = context.appConfig[context.global.appMode];
  const xScale = context.dotPlotConfig.xScale;
  const yScale = context.dotPlotConfig.yScale;
  let rng = seedrandom(d[dataset["primaryKey"]] + context.global["participantId"]);

  let xBase, xBound;
  if (!xIsNA) {
    xBase = xScale(d["xVar"]) + xScale.bandwidth() / 2;
    xBound = CAT_JITTER * xScale.bandwidth();
  } else {
    xBase = 0.5 * yScale.bandwidth(); // align left (matches the old bubble)
    xBound = CAT_JITTER * yScale.bandwidth();
  }
  let yBase, yBound;
  if (!yIsNA) {
    yBase = yScale(d["yVar"]) + yScale.bandwidth() / 2;
    yBound = CAT_JITTER * yScale.bandwidth();
  } else {
    yBase = context.plotHeight - 0.5 * xScale.bandwidth(); // align bottom (old bubble)
    yBound = CAT_JITTER * xScale.bandwidth();
  }

  // Independent per-axis offset (same sign-then-magnitude draw pattern as scatter).
  let sign = rng() > 0.5 ? -1 : 1;
  let x = xBase + sign * rng() * xBound;
  sign = rng() > 0.5 ? -1 : 1;
  let y = yBase + sign * rng() * yBound;

  // Reflect any overflow back inside the plot area (mirror), matching scatter.
  if (x < 0) x = -x;
  else if (x > context.plotWidth) x = 2 * context.plotWidth - x;
  if (y < 0) y = -y;
  else if (y > context.plotHeight) y = 2 * context.plotHeight - y;

  // Categorical axes have no scale.invert(); the single-item Details panel must
  // show the true category, so clear any stored data-space jitter. This upholds
  // the scatter panel's invariant (jitter_*Val non-null IFF a Q jitter applied),
  // so panelValue() falls back to the true value for both axes.
  d["jitter_xVal"] = null;
  d["jitter_yVal"] = null;

  return `translate(${x}, ${y})`;
}
