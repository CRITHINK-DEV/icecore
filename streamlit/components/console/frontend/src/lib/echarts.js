/* ECharts 6 with its v5 theme (the approved look), tree-shaken to what prototype D draws: bar, line and scatter series on category/value/log axes,
   with tooltip, legend and markLine. Same API as the prototype's global `echarts`. */
import * as echarts from 'echarts/core';
import { BarChart, LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import 'echarts/theme/v5';

echarts.use([BarChart, LineChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);
export default echarts;
