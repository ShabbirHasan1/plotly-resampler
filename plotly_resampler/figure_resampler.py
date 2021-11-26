# -*- coding: utf-8 -*-
"""
Wrapper around the the plotly go.Figure class which allows bookkeeping and
back-end based resampling of high-frequency sequential data.

Notes
-----
* The term `high-frequency` actually refers very large amounts of data, see also<br>
  https://www.sciencedirect.com/topics/social-sciences/high-frequency-data

"""
__author__ = "Jonas Van Der Donckt, Jeroen Van Der Donckt, Emiel Deprost"

import re
from typing import List, Optional, Union, Iterable, Tuple, Dict
from uuid import uuid4

import dash_bootstrap_components as dbc
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from dash import dcc
from dash.dependencies import Input, Output, State
from jupyter_dash import JupyterDash

from .downsamplers import AbstractSeriesDownsampler, EveryNthPoint


class FigureResampler(go.Figure):
    """Mirrors the go.Figure's `data` attribute to allow resampling in the back-end."""

    def __init__(
        self,
        figure: go.Figure = go.Figure(),
        default_n_shown_samples: int = 1000,
        default_downsampler: AbstractSeriesDownsampler = EveryNthPoint(),
        resampled_trace_prefix_suffix: Tuple[str, str] = (
            '<b style="color:sandybrown">[R]</b> ',
            "",
        ),
        verbose: bool = False,
    ):
        """Instantiate a resampling data mirror.

        Parameters
        ----------
        figure: go.Figure
            The figure that will be decorated.
        default_n_shown_samples: int, optional
            The default number of samples that will be shown for each trace,
            by default 1000.<br>
            * **Note**, this can be overriden within the `add_trace()` method.<br>
        default_downsampler: AbstractSeriesDownsampler
            An instance which implements the AbstractSeriesDownsampler interface,
            by default `EveryNthPoint`.
            This will be used as default downsampler.<br>
            * **Note**, this can be overriden within the `add_trace()` method.<br>
        resampled_trace_prefix_suffix: str, optional
            A tuple which contains the `prefix` and `suffix`, respectively, which
            will be added to the trace its name when a resampled version of the trace
            is shown, by default a bold, orange `[R]` is shown as prefix
            (no suffix is shown).
        verbose: bool, optional
            Whether some verbose messages will be printed or not, by default False.

        """
        self._hf_data: Dict[str, dict] = {}
        self._global_n_shown_samples = default_n_shown_samples
        self._print_verbose = verbose
        assert len(resampled_trace_prefix_suffix) == 2
        self._prefix, self._suffix = resampled_trace_prefix_suffix

        self._global_downsampler = default_downsampler

        super().__init__(figure)

    def _print(self, *values):
        """Helper method for printing if `verbose` is set to True."""
        if self._print_verbose:
            print(*values)

    def _query_hf_data(self, trace: dict) -> Optional[dict]:
        """Query the internal `_hf_data` attribute and returns a match based on `uid`.

        Parameters
        ----------
        trace : dict
            The trace where we want to find a match for.

        Returns
        -------
        Optional[dict]
            The `hf_data`-trace dict if a match is found, else `None`.

        """
        if isinstance(trace, dict):
            uid = trace.get("uid")
        else:
            uid = trace['uid']
        trace_data = self._hf_data.get(uid)
        if trace_data is None:
            trace_props = {
                k: trace[k] for k in set(trace.keys()).difference({"x", "y"})
            }
            self._print(f"[W] trace with {trace_props} not found")
        return trace_data

    def check_update_trace_data(self, trace, start=None, end=None):
        """Check and updates the passed`trace`.

        Note
        ----
        This is a pass by reference. The passed trace object will be updated.
        No new view of this trace will be created!

        Parameters
        ----------
        trace : BaseTraceType or dict
             - An instances of a trace class from the plotly.graph_objs
                package (e.g plotly.graph_objs.Scatter, plotly.graph_objs.Bar)
              - or a dicts where:

                  - The 'type' property specifies the trace type (e.g.
                    'scatter', 'bar', 'area', etc.). If the dict has no 'type'
                    property then 'scatter' is assumed.
                  - All remaining properties are passed to the constructor
                    of the specified trace type.
        start : Union[float, str], optional
            The start index for which we want resampled data to be updated to,
            by default None,
        end : Union[float, str], optional
            The end index for which we want the resampled data to be updated to,
            by default None

        Notes
        -----
        * If `start` and `stop` are strings, they most likely represent time-strings
        * `start` and `stop` will always be of the same type (float / time-string)
           because their underlying axis is the same.

        """
        hf_data = self._query_hf_data(trace)
        if hf_data is not None:
            axis_type = hf_data["axis_type"]
            if axis_type == "date":
                hf_series = self._slice_time(
                    hf_data["hf_series"], pd.to_datetime(start), pd.to_datetime(end)
                )
            else:
                hf_series: pd.Series = hf_data["hf_series"]
                start = hf_series.index[0] if start is None else start
                end = hf_series.index[-1] if end is None else end
                if isinstance(hf_series.index, (pd.Int64Index, pd.UInt64Index)):
                    start = round(start)
                    end = round(end)

                # Search the index-positions
                start_idx, end_idx = np.searchsorted(hf_series.index, [start, end])
                hf_series = hf_series.iloc[start_idx:end_idx]

            # Add a prefix when the original data is downsampled
            name: str = trace["name"]
            if len(hf_series) > hf_data["max_n_samples"]:
                name = ("" if name.startswith(self._prefix) else self._prefix) + name
                name += self._suffix if not name.endswith(self._suffix) else ""
                trace["name"] = name
            else:
                if len(self._prefix) and name.startswith(self._prefix):
                    trace["name"] = name[len(self._prefix):]
                if len(self._suffix) and name.endswith(self._suffix):
                    trace["name"] = name[: -len(self._suffix)]

            # Downsample the data and store it in the trace-fields
            downsampler: AbstractSeriesDownsampler = hf_data["downsampler"]
            s_res: pd.Series = downsampler.downsample(
                hf_series, hf_data["max_n_samples"]
            )
            trace["x"] = s_res.index
            trace["y"] = s_res.values

            # Check if hovertext also needs to be resampled
            hovertext = hf_data.get("hovertext")
            if isinstance(hovertext, pd.Series):
                trace["hovertext"] = pd.merge_asof(
                    s_res,
                    hovertext,
                    left_index=True,
                    right_index=True,
                    direction="nearest",
                )[hovertext.name].values
            else:
                trace["hovertext"] = hovertext
        else:
            self._print("hf_data not found")

    def check_update_figure_dict(
        self,
        figure: dict,
        start: Optional[Union[float, str]] = None,
        stop: Optional[Union[float, str]] = None,
        xaxis_filter: str = None,
    ):
        """Check and update the traces within the figure dict.

        This method will most likely be used within a `Dash` callback to resample the
        view, based on the configured number of parameters.

        Note
        ----
        This is a pass by reference. The passed figure object will be updated.
        No new view of this figure will be created, hence no return!

        Parameters
        ----------
        figure : dict
            The figure dict which will be updated.
        start : Union[float, str], optional
            The start time for the new resampled data view, by default None.
        stop : Union[float, str], optional
            The end time for the new resampled data view, by default None.
        xaxis_filter: str, Optional
            Additional trace-update subplot filter.

        """
        xaxis_filter_short = None
        if xaxis_filter is not None:
            xaxis_filter_short = "x" + xaxis_filter.lstrip("xaxis_filter")

        for trace in figure["data"]:
            if xaxis_filter is not None:
                # the x-anchor of the trace is stored in the layout data
                if trace.get("yaxis") is None:
                    # no yaxis -> we make the assumption that yaxis = xaxis_filter_short
                    y_axis = "y" + xaxis_filter[1:]
                else:
                    y_axis = "yaxis" + trace.get("yaxis")[1:]
                x_anchor_trace = figure["layout"][y_axis].get("anchor")

                # we skip when:
                # * the change was made on the first row and the trace its anchor is not
                #   in [None, 'x']
                #   -> why None: traces without row/col argument and stand on first row
                #      and do not have the anchor property (hence the DICT.get() method)
                # * x-anchor-trace != xaxis_filter-short for NON first rows
                if (
                    xaxis_filter_short == "x" and x_anchor_trace not in [None, "x"]
                ) or (
                    xaxis_filter_short != "x" and x_anchor_trace != xaxis_filter_short
                ):
                    continue
            self.check_update_trace_data(trace=trace, start=start, end=stop)

    @staticmethod
    def _slice_time(
        hf_series: pd.Series,
        t_start: Optional[pd.Timestamp] = None,
        t_stop: Optional[pd.Timestamp] = None,
    ) -> pd.Series:
        """Slice the time-indexed `hf_series` for the passed pd.Timestamps.

        Note
        ----
        This returns a **view** of hf_series!

        Parameters
        ----------
        hf_series: pd.Series
            The **datetime-indexed** series, which will be sliced.
        t_start: pd.Timestamp, optional
            The lower-time-bound of the slice, if set to None, no lower-bound threshold
            will be applied, by default None.
        t_stop:  pd.Timestamp, optional
            The upper time-bound of the slice, if set to None, no upper-bound threshold
            will be applied, by default None.

        Returns
        -------
        pd.Series
            The sliced **view** of the series.

        """
        def to_same_tz(
            ts: Union[pd.Timestamp, None], reference_tz=hf_series.index.tz
        ) -> Union[pd.Timestamp, None]:
            """Adjust `ts` its timezone to the `reference_tz`."""
            if ts is None:
                return None
            elif reference_tz is not None:
                if ts.tz is not None:
                    assert ts.tz.zone == reference_tz.zone
                    return ts
                else:  # localize -> time remains the same
                    return ts.tz_localize(reference_tz)
            elif reference_tz is None and ts.tz is not None:
                return ts.tz_localize(None)
            return ts

        return hf_series[to_same_tz(t_start): to_same_tz(t_stop)]

    def add_trace(
        self,
        trace,
        max_n_samples: int = None,
        downsampler: AbstractSeriesDownsampler = None,
        limit_to_view: bool = False,
        # Use these if you want some speedups (and are working with really large data)
        hf_x: Iterable = None,
        hf_y: Iterable = None,
        hf_hovertext: Union[str, Iterable] = None,
        **trace_kwargs,
    ):
        """Add a trace to the figure.

        Note
        ----
        Constructing traces with **very large data amounts** really takes some time.
        To speed up this `add_trace()` method -> just create a trace with no data
        (empty lists) and pass the high frequency data to this method,
        using the `hf_x` and `hf_y` parameters. See the example below:
        >>> from plotly.subplots import make_subplots
        >>> s = pd.Series()  # a high-frequency series, with more than 1e7 samples
        >>> fig = FigureResampler(go.Figure())
        >>> fig.add_trace(go.Scattergl(x=[], y=[], ...), hf_x=s.index, hf_y=s)

        Notes
        -----
        * **Pro tip**: if you do `not want to downsample` your data, set `max_n_samples`
          to the size of your trace!
        * Low-frequency time-series data (e.g., a scatter of detected peaks), can hinder
          the the automatic-zoom (y-scaling) functionality as these will not be stored
          in the back-end datamirror and thus not be scaled to the view.<br>
          To circumvent this, the `limit_to_view` argument can be set, which forces
          these low-frequency series to be also stored in the back-end.
        * `hf_x`, `hf_y`, and 'hf_hovertext` are useful when you deal with large amounts
          of data (as it can increase the speed of this add_trace() method with ~30%)
          Note: These arguments have priority over the trace's data and (hover)text
          attributes.

        Parameters
        ----------
        trace : BaseTraceType or dict
            Either:
              - An instances of a trace class from the plotly.graph_objs
                package (e.g plotly.graph_objs.Scatter, plotly.graph_objs.Bar)
              - or a dict where:

                  - The 'type' property specifies the trace type (e.g.
                    'scatter', 'bar', 'area', etc.). If the dict has no 'type'
                    property then 'scatter' is assumed.
                  - All remaining properties are passed to the constructor
                    of the specified trace type.
        max_n_samples : int, optional
            The maximum number of samples that will be shown by the trace.\n
            .. note::
                If this variable is not set; `_global_n_shown_samples` will be used.
        downsampler: AbstractSeriesDownsampler, optional
            The abstract series downsampler method
        limit_to_view: boolean, optional
            If set to True the trace's datapoints will be cut to the corresponding
            front-end view, even if the total number of samples is lower than
            `max_n_samples`, By default False.
        hf_x: Iterable, optional
            The original high frequency series positions, can be either a time-series or
            an increasing, numerical index. If set, this has priority over the trace its
            data.
        hf_y: Iterable, optional
            The original high frequency values. If set, this has priority over the
            trace its data.
        hf_hovertext: Iterable, optional
            The original high frequency hovertext. If set, this has priority over the
            `text` or `hovertext` argument.
        **trace_kwargs:
            Additional trace related keyword arguments.<br>
            e.g.: row=.., col=..., secondary_y=...,<br>
            see trace_docs: https://plotly.com/python-api-reference/generated/plotly.graph_objects.Figure.html#plotly.graph_objects.Figure.add_traces

        Returns
        -------
        BaseFigure
            The Figure on which add_trace was called on.

        """
        if max_n_samples is None:
            max_n_samples = self._global_n_shown_samples

        # First add the trace, as each (even the non-hf_data traces), must contain this
        # key for comparison
        trace.uid = str(uuid4())

        high_frequency_traces = ["scatter", "scattergl"]
        if trace["type"].lower() in high_frequency_traces:
            hf_x = (
                trace["x"]
                if hf_x is None
                else hf_x.values if isinstance(hf_x, pd.Series)
                else hf_x
            )
            hf_y = (
                trace["y"]
                if hf_y is None
                else hf_y.values if isinstance(hf_y, pd.Series)
                else hf_y
            )
            hf_hovertext = (
                hf_hovertext if hf_hovertext is not None
                else trace["hovertext"] if trace["hovertext"] is not None
                else trace["text"]
            )

            # Make sure to set the text-attribute to None as the default plotly behavior
            # for these high-dimensional traces (scatters) is that text will be shown in
            # hovertext and not in on-graph texts (as is the case with bar-charts)
            trace["text"] = None

            # Remove NaNs for efficiency (storing less meaningless data)
            # NaNs introduce gaps between enclosing non-NaN datapoints & might distort
            # the resampling algorithms
            try:
                nan_values_mask = ~np.isnan(hf_y)
                hf_x = hf_x[nan_values_mask]
                hf_y = hf_y[nan_values_mask]
                # orjson encoding doesn't like to encode with uint8 & uint16 dtype
                if isinstance(hf_y, (pd.Series, np.ndarray)):
                    if str(hf_y.dtype) in ["uint8", "uint16"]:
                        hf_y = hf_y.astype("uint32")

                # Note: this also converts hf_hovertext to a np.ndarray
                if isinstance(hf_hovertext, (list, np.ndarray, pd.Series)):
                    hf_hovertext = np.array(hf_hovertext)[nan_values_mask]
            except:
                pass

            assert len(hf_x) > 0
            assert len(hf_x) == len(hf_y)

            # Convert the hovertext to a pd.Series if it's now a np.ndarray
            # Note: The size of hovertext must be the same size as hf_x otherwise a
            #   ValueError will be thrown
            if isinstance(hf_hovertext, np.ndarray):
                hf_hovertext = pd.Series(
                    data=hf_hovertext, index=hf_x, copy=False, name="hovertext"
                )

            n_samples = len(hf_x)
            # These traces will determine the autoscale RANGE!
            #   -> so also store when `limit_to_view` is set.
            if n_samples > max_n_samples or limit_to_view:
                self._print(
                    f"\t[i] DOWNSAMPLE {trace['name']}\t{n_samples}->{max_n_samples}"
                )

                # We will re-create this each time as hf_x and hf_y withholds
                # high-frequency data
                hf_series = pd.Series(data=hf_y, index=hf_x, copy=False).rename("data")
                hf_series.index.rename("timestamp", inplace=True)

                # Checking this now avoids less interpretable `KeyError` when resampling
                assert hf_series.index.is_monotonic_increasing

                # As we support prefix-suffixing of downsampled data, we assure that
                # each trace has a name
                # https://github.com/plotly/plotly.py/blob/ce0ed07d872c487698bde9d52e1f1aadf17aa65f/packages/python/plotly/plotly/basedatatypes.py#L539
                # The link above indicates that the trace index is derived from `data`
                if trace.name is None:
                    trace.name = f"trace {len(self.data)}"

                # Determine (1) the axis type and (2) the downsampler instance
                # & (3) store a hf_data entry for the corresponding trace,
                # identified by its UUID
                axis_type = "date" if isinstance(hf_x, pd.DatetimeIndex) else "linear"
                d = self._global_downsampler if downsampler is None else downsampler
                self._hf_data[trace.uid] = {
                    "max_n_samples": max_n_samples,
                    "hf_series": hf_series,
                    "axis_type": axis_type,
                    "downsampler": d,
                    "hovertext": hf_hovertext,
                }

                # NOTE: if all the raw data needs to be sent to the javascript, and
                #  the trace is truly high-frequency, this would take significant time!
                #  hence, you first downsample the trace.
                self.check_update_trace_data(trace)
                super().add_trace(trace=trace, **trace_kwargs)
            else:
                self._print(f"[i] NOT resampling {trace['name']} - len={n_samples}")
                trace.x = hf_x
                trace.y = hf_y
                trace.text = hf_hovertext
                return super().add_trace(trace=trace, **trace_kwargs)
        else:
            self._print(f"trace {trace['type']} is not a high-frequency trace")

            # hf_x and hf_y have priority over the traces' data
            trace["x"] = trace["x"] if hf_x is None else hf_x
            trace["y"] = trace["y"] if hf_y is None else hf_y
            assert len(trace["x"]) > 0
            assert len(trace["x"] == len(trace["y"]))
            return super().add_trace(trace=trace, **trace_kwargs)

    def show_dash(self, mode=None, **kwargs):
        """Show the figure in a Dash web-app.

        Parameters
        ----------
        mode: str, optional
            Display mode. One of: \n
            * ``"external"``: The URL of the app will be displayed in the notebook
                output cell. Clicking this URL will open the app in the default
                web browser.<br>
            * ``"inline"``: The app will be displayed inline in the notebook output cell
                in an iframe.<br>
            * ``"jupyterlab"``: The app will be displayed in a dedicate tab in the
                JupyterLab interface. Requires JupyterLab and the `jupyterlab-dash`
                extension.<br>
            By default None, which will result in the same behavior as ``"external"``.
        kwargs:
            Additional app.run_server() kwargs.<br>
            e.g.: port

        """
        app = JupyterDash("local_app")
        app.layout = dbc.Container(dcc.Graph(id="resampled-graph", figure=self))

        @app.callback(
            Output("resampled-graph", "figure"),
            Input("resampled-graph", "relayoutData"),
            State("resampled-graph", "figure"),
        )
        def update_graph(changed_layout: dict, current_graph):
            if changed_layout:
                self._print("-" * 100 + "\n", "changed layout", changed_layout)
                # for debugging purposes; uncomment the line below and save fig dict
                # TODO -> when verbose maybe save _fig_dict
                if self._print_verbose:
                    self._fig_dict = current_graph

                def get_matches(regex: re.Pattern, strings: Iterable[str]) -> List[str]:
                    """Returns all the items in `strings` which regex.match `regex`."""
                    matches = []
                    for item in strings:
                        m = regex.match(item)
                        if m is not None:
                            matches.append(m.string)
                    return sorted(matches)

                # Determine the start & end regex matches
                cl_keys = changed_layout.keys()
                start_matches = get_matches(re.compile(r"xaxis\d*.range\[0]"), cl_keys)
                stop_matches = get_matches(re.compile(r"xaxis\d*.range\[1]"), cl_keys)
                if len(start_matches) and len(stop_matches):
                    for t_start_key, t_stop_key in zip(start_matches, stop_matches):
                        # Check if the xaxis<NUMB> part of xaxis<NUMB>.[0-1] matches
                        assert t_start_key.split(".")[0] == t_stop_key.split(".")[0]
                        self.check_update_figure_dict(
                            current_graph,
                            start=changed_layout[t_start_key],
                            stop=changed_layout[t_stop_key],
                            xaxis_filter=t_start_key.split(".")[0],
                        )
                elif len(get_matches(re.compile(r"xaxis\d*.autorange"), cl_keys)):
                    # Autorange is applied on all axes -> no xaxis_filter argument
                    self.check_update_figure_dict(current_graph)
            return current_graph

        # If figure height is specified -> re-use is for inline dash app height
        if (
            self.layout.height is not None
            and mode == "inline"
            and "height" not in kwargs
        ):
            kwargs["height"] = self.layout.height + 18
        app.run_server(mode=mode, **kwargs)