/* ECharts 5.5.0, tree-shaken to what prototype D draws: bar, line and scatter series on category/value/log axes,
   with tooltip, legend and markLine. Same API as the prototype's global `echarts`. */
import * as echarts from 'echarts/core';
import { BarChart, LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([BarChart, LineChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);
export default echarts;
