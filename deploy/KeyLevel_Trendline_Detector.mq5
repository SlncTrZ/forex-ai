//+------------------------------------------------------------------+
//|                                     KeyLevel_Trendline_Detector.mq5|
//|                                  Copyright 2026, MeiLin & SlncTrZ|
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, MeiLin & SlncTrZ"
#property link      "https://github.com/SlncTrZ/Forex-AI"
#property version   "1.00"
#property indicator_chart_window
#property indicator_buffers 2
#property indicator_plots   2

// Plot 1: Key Resistance / Swing Highs
#property indicator_label1  "KeyLevel Resistance"
#property indicator_type1   PLOT_LINE
#property indicator_color1  clrCrimson
#property indicator_style1  STYLE_SOLID
#property indicator_width1  2

// Plot 2: Key Support / Swing Lows
#property indicator_label2  "KeyLevel Support"
#property indicator_type2   PLOT_LINE
#property indicator_color2  clrDodgerBlue
#property indicator_style2  STYLE_SOLID
#property indicator_width2  2

// Input parameters
input int      InpLeftBars       = 15;        // Left confirmation bars
input int      InpRightBars      = 5;         // Right confirmation bars
input int      InpH1_FastEMA     = 50;        // H1 Trend Bias Fast EMA
input int      InpH1_SlowEMA     = 200;       // H1 Trend Bias Slow EMA
input bool     InpDrawTrendlines = true;      // Auto-draw Trendlines on chart
input color    InpTrendlineColor = clrGold;   // Trendline color

// Indicator buffers
double BufferResistance[];
double BufferSupport[];

// Trendline tracking struct
struct SwingPoint
{
   datetime time;
   double   price;
   int      type; // 1 = High, -1 = Low
};

SwingPoint LastHighs[5];
SwingPoint LastLows[5];
int TotalHighs = 0;
int TotalLows = 0;

// Handles
int h_h1_fast = INVALID_HANDLE;
int h_h1_slow = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, BufferResistance, INDICATOR_DATA);
   SetIndexBuffer(1, BufferSupport, INDICATOR_DATA);
   
   ArraySetAsSeries(BufferResistance, true);
   ArraySetAsSeries(BufferSupport, true);
   
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, 0.0);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, 0.0);
   
   // Handle H1 Bias
   h_h1_fast = iMA(_Symbol, PERIOD_H1, InpH1_FastEMA, 0, MODE_EMA, PRICE_CLOSE);
   h_h1_slow = iMA(_Symbol, PERIOD_H1, InpH1_SlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Custom indicator deinitialization function                       |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, "TL_KeyLevel_");
   if(h_h1_fast != INVALID_HANDLE) IndicatorRelease(h_h1_fast);
   if(h_h1_slow != INVALID_HANDLE) IndicatorRelease(h_h1_slow);
}

//+------------------------------------------------------------------+
//| Custom indicator iteration function                              |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   if(rates_total < InpLeftBars + InpRightBars + 10) return(0);
   
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(close, true);
   ArraySetAsSeries(time, true);
   
   int limit = rates_total - prev_calculated;
   if(limit > rates_total - InpLeftBars - InpRightBars - 1)
      limit = rates_total - InpLeftBars - InpRightBars - 1;
      
   double cur_resistance = (prev_calculated > 0) ? BufferResistance[1] : 0.0;
   double cur_support    = (prev_calculated > 0) ? BufferSupport[1] : 0.0;
   
   for(int i = limit; i >= 0; i--)
   {
      int candidate_idx = i + InpRightBars;
      double candidate_high = high[candidate_idx];
      double candidate_low  = low[candidate_idx];
      
      bool is_swing_high = true;
      bool is_swing_low  = true;
      
      // Check Left Bars
      for(int k = 1; k <= InpLeftBars; k++)
      {
         if(candidate_idx + k >= rates_total) break;
         if(high[candidate_idx + k] >= candidate_high) is_swing_high = false;
         if(low[candidate_idx + k] <= candidate_low)   is_swing_low  = false;
      }
      
      // Check Right Bars
      for(int k = 1; k <= InpRightBars; k++)
      {
         if(high[candidate_idx - k] > candidate_high) is_swing_high = false;
         if(low[candidate_idx - k] < candidate_low)   is_swing_low  = false;
      }
      
      if(is_swing_high)
      {
         cur_resistance = candidate_high;
         AddSwingPoint(time[candidate_idx], candidate_high, 1);
         if(InpDrawTrendlines) DrawTrendline(1);
      }
      if(is_swing_low)
      {
         cur_support = candidate_low;
         AddSwingPoint(time[candidate_idx], candidate_low, -1);
         if(InpDrawTrendlines) DrawTrendline(-1);
      }
      
      BufferResistance[i] = cur_resistance;
      BufferSupport[i]    = cur_support;
   }
   
   return(rates_total);
}

//+------------------------------------------------------------------+
//| Add swing point to memory                                        |
//+------------------------------------------------------------------+
void AddSwingPoint(datetime t, double p, int type)
{
   if(type == 1)
   {
      for(int i = 4; i > 0; i--) LastHighs[i] = LastHighs[i - 1];
      LastHighs[0].time = t;
      LastHighs[0].price = p;
      LastHighs[0].type = 1;
      if(TotalHighs < 5) TotalHighs++;
   }
   else
   {
      for(int i = 4; i > 0; i--) LastLows[i] = LastLows[i - 1];
      LastLows[0].time = t;
      LastLows[0].price = p;
      LastLows[0].type = -1;
      if(TotalLows < 5) TotalLows++;
   }
}

//+------------------------------------------------------------------+
//| Draw Trendline connecting 2 most recent swings                  |
//+------------------------------------------------------------------+
void DrawTrendline(int type)
{
   string name = (type == 1) ? "TL_KeyLevel_Resistance" : "TL_KeyLevel_Support";
   SwingPoint pt1, pt2;
   
   if(type == 1 && TotalHighs >= 2)
   {
      pt1 = LastHighs[1]; // Older high
      pt2 = LastHighs[0]; // Newer high
   }
   else if(type == -1 && TotalLows >= 2)
   {
      pt1 = LastLows[1]; // Older low
      pt2 = LastLows[0]; // Newer low
   }
   else return;
   
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_TREND, 0, pt1.time, pt1.price, pt2.time, pt2.price);
      ObjectSetInteger(0, name, OBJPROP_COLOR, (type == 1) ? clrCrimson : clrDodgerBlue);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_DASH);
      ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, true);
   }
   else
   {
      ObjectMove(0, name, 0, pt1.time, pt1.price);
      ObjectMove(0, name, 1, pt2.time, pt2.price);
   }
}
//+------------------------------------------------------------------+
