//+------------------------------------------------------------------+
//| GoldAnalyst_MT4_Feed.mq4                                          |
//| Read-only market data bridge. NEVER places or modifies orders.   |
//| Configure a unique secret; allow the Railway HTTPS URL in MT4.   |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Sends broker bid/ask + D1/H4/H1 candles to Gold Analyst Pro via HTTPS."
#property description "READ ONLY: no OrderSend, OrderClose or account data."

input string BridgeURL = "https://analyst-multiasset-production.up.railway.app/api/mt4/ingest";
input string BridgeToken = "";  // Same secret as MT4_BRIDGE_TOKEN on Railway (private)
input int    SendEverySeconds = 45;
input int    BarsPerTimeframe = 75; // allowed 45-120
input string Broker_XAUUSD = "XAUUSD";
input string Broker_XAGUSD = "XAGUSD";
input string Broker_EURUSD = "EURUSD";
input string Broker_GBPUSD = "GBPUSD";
input string Broker_USDJPY = "USDJPY";
input string Broker_USOIL = "USOIL"; // Might be WTI, USOil.cash, etc.

string instruments[6];
string broker_symbols[6];
datetime observed_tick[6];
datetime last_change_utc[6];

string EscapeJson(string s)
{
   StringReplace(s,"\\","\\\\");
   StringReplace(s,"\"","\\\"");
   return s;
}

// Include strictly broker-time labels; these are NOT UTC timestamps.
string CandleArray(string broker, ENUM_TIMEFRAMES tf, int digits)
{
   MqlRates rates[];
   ArraySetAsSeries(rates,true);
   ResetLastError();
   int count=CopyRates(broker,tf,0,BarsPerTimeframe,rates);
   if(count<45)
   {
      Print("MT4 Bridge: history missing for ",broker," / ",IntegerToString((int)tf),
            " bars=",count," error=",GetLastError(),". Open chart and load history.");
      return "";
   }
   string output="[";
   // With ArraySetAsSeries true: rates[0] = newest, so serialize oldest -> newest.
   for(int i=count-1;i>=0;i--)
   {
      if(i<count-1) output+=",";
      output+="{\"time\":\""+TimeToString(rates[i].time,TIME_DATE|TIME_SECONDS)+"\"";
      output+=",\"open\":"+DoubleToString(rates[i].open,digits);
      output+=",\"high\":"+DoubleToString(rates[i].high,digits);
      output+=",\"low\":"+DoubleToString(rates[i].low,digits);
      output+=",\"close\":"+DoubleToString(rates[i].close,digits)+"}";
   }
   output+="]";
   return output;
}

bool SendInstrument(int i)
{
   string symbol=broker_symbols[i];
   if(symbol=="") return false;
   if(!SymbolSelect(symbol,true))
   {
      Print("MT4 Bridge: symbol unavailable in this broker: ",symbol);
      return false;
   }

   MqlTick tick;
   if(!SymbolInfoTick(symbol,tick) || tick.bid<=0 || tick.ask<=0 || tick.ask<tick.bid || tick.time<=0)
   {
      Print("MT4 Bridge: no valid quote for ",symbol);
      return false;
   }

   datetime current_utc=TimeGMT(); // Uses PC/VPS clock: keep Windows time synchronized.
   // On the very first observation do NOT send an old tick cached over the weekend.
   if(observed_tick[i]==0)
   {
      observed_tick[i]=tick.time;
      last_change_utc[i]=0;
      Print("MT4 Bridge: ",symbol," initialized; waiting for a NEW tick before first send.");
      return false;
   }
   if(tick.time!=observed_tick[i])
   {
      observed_tick[i]=tick.time;
      last_change_utc[i]=current_utc;
   }
   if(last_change_utc[i]<=0) return false;
   int tick_age=(int)(current_utc-last_change_utc[i]);
   if(tick_age<0 || tick_age>85) // below server's default 120-second limit
   {
      Print("MT4 Bridge: stale quote for ",symbol," age ",tick_age," sec; skipped.");
      return false;
   }

   int digits=(int)MarketInfo(symbol,MODE_DIGITS);
   if(digits<0 || digits>8) return false;
   string d1=CandleArray(symbol,PERIOD_D1,digits);
   string h4=CandleArray(symbol,PERIOD_H4,digits);
   string h1=CandleArray(symbol,PERIOD_H1,digits);
   if(d1=="" || h4=="" || h1=="") return false;

   string body="{\"instrument\":\""+EscapeJson(instruments[i])+"\"";
   body+=",\"broker_symbol\":\""+EscapeJson(symbol)+"\"";
   body+=",\"bid\":"+DoubleToString(tick.bid,digits);
   body+=",\"ask\":"+DoubleToString(tick.ask,digits);
   body+=",\"digits\":"+IntegerToString(digits);
   body+=",\"sent_at_utc\":"+IntegerToString((int)current_utc);
   body+=",\"tick_age_seconds\":"+IntegerToString(tick_age);
   body+=",\"tick_time_broker\":\""+TimeToString(tick.time,TIME_DATE|TIME_SECONDS)+"\"";
   body+=",\"frames\":{\"D1\":"+d1+",\"H4\":"+h4+",\"H1\":"+h1+"}}";

   char payload[], response[];
   string responseHeaders;
   // All payload content is ASCII; explicit length excludes null terminator.
   int bytes=StringToCharArray(body,payload,0,StringLen(body),CP_UTF8);
   if(bytes<=0) return false;
   ArrayResize(payload,bytes);

   string headers="Content-Type: application/json\r\n"+
                  "X-MT4-Token: "+BridgeToken+"\r\n";
   ResetLastError();
   int status=WebRequest("POST",BridgeURL,headers,9000,payload,response,responseHeaders);
   if(status==200)
   {
      Print("MT4 Bridge: OK ",instruments[i]," (",symbol,") - market data sent.");
      return true;
   }
   if(status==-1)
      Print("MT4 Bridge: WebRequest failed for ",symbol," MT4 error ",GetLastError(),
            ". Check Tools -> Options -> Expert Advisors -> Allow WebRequest URL.");
   else
      Print("MT4 Bridge: server HTTP ",status," for ",symbol,
            ". Check MT4_BRIDGE_TOKEN, Market Watch and UTC clock.");
   return false;
}

int OnInit()
{
   if(StringFind(BridgeURL,"https://")!=0 || StringLen(BridgeToken)<32)
   {
      Print("MT4 Bridge: require HTTPS BridgeURL and unique token of 32+ chars.");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(SendEverySeconds<15 || SendEverySeconds>300 || BarsPerTimeframe<45 || BarsPerTimeframe>120)
   {
      Print("MT4 Bridge: interval must be 15-300 sec, bars 45-120.");
      return(INIT_PARAMETERS_INCORRECT);
   }

   instruments[0]="XAUUSD";broker_symbols[0]=Broker_XAUUSD;
   instruments[1]="XAGUSD";broker_symbols[1]=Broker_XAGUSD;
   instruments[2]="EURUSD";broker_symbols[2]=Broker_EURUSD;
   instruments[3]="GBPUSD";broker_symbols[3]=Broker_GBPUSD;
   instruments[4]="USDJPY";broker_symbols[4]=Broker_USDJPY;
   instruments[5]="USOIL"; broker_symbols[5]=Broker_USOIL;

   for(int i=0;i<6;i++)
   {
      observed_tick[i]=0;
      last_change_utc[i]=0;
      if(broker_symbols[i]!="" && !SymbolSelect(broker_symbols[i],true))
         Print("MT4 Bridge: cannot select ",broker_symbols[i],"; check broker symbol names.");
   }
   if(!EventSetTimer(SendEverySeconds))
   {
      Print("MT4 Bridge: timer initialization failed, error ",GetLastError());
      return(INIT_FAILED);
   }
   Print("MT4 Bridge: started (READ ONLY). Do NOT share your BridgeToken.");
   return(INIT_SUCCEEDED);
}

void OnTimer()
{
   if(!IsConnected())
   {
      Print("MT4 Bridge: terminal disconnected; no quote sent.");
      return;
   }
   for(int i=0;i<6;i++) SendInstrument(i);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}
//+------------------------------------------------------------------+
