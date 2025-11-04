import React, { useState, useEffect, useRef } from 'react';
import { ethers } from 'ethers';
import {
  Settings,
  Power,
  Terminal,
  AlertTriangle,
  TrendingUp,
  TrendingDown,
  DollarSign,
  Activity,
  Zap,
  RefreshCw,
  Play,
  Pause,
  BarChart3,
  BookOpen,
  Heart,
  Users,
  Globe,
  HandHeart,
  ChevronLeft,
  ChevronRight
} from 'lucide-react';

import { useWallet } from '../hooks/useWallet';
import { orderbookApi } from '../lib/api';
import { useTrade } from '../hooks/useTrade';
import { useToast } from './ui/use-toast';
import { resolveSettlementAddress, resolveTokenAddress, CHAIN_REGISTRY, HEDERA_TESTNET } from '../lib/contracts';
import { priceService, type PriceData, PriceService } from '../lib/priceService';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip } from 'recharts';
import LabelTerminal from './ui/label-terminal';
import { AssetList } from './ui/asset-select';
import { NetworkList } from './ui/network-select';
import BalancePanel from './BalancePanel';
import { TradingViewChart } from './TradingViewChart';

// API moved to lib/api.ts

// UI Components
const Button = ({ children, variant = 'default', size = 'default', className = '', disabled = false, onClick, ...props }) => {
  const variants = {
    default: 'bg-primary hover:bg-primary/90 text-primary-foreground shadow-sm',
    destructive: 'bg-destructive hover:bg-destructive/90 text-destructive-foreground shadow-sm',
    outline: 'border border-border bg-background hover:bg-accent hover:text-accent-foreground',
    secondary: 'bg-secondary hover:bg-secondary/80 text-secondary-foreground shadow-sm',
    ghost: 'hover:bg-accent hover:text-accent-foreground',
    success: 'bg-neobanka-success hover:bg-neobanka-success/90 text-white shadow-sm'
  };

  const sizes = {
    default: 'h-9 px-4 py-2',
    sm: 'h-8 rounded-md px-3 text-xs',
    lg: 'h-10 rounded-md px-8'
  };

  return (
    <button
      className={`inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 ${variants[variant]} ${sizes[size]} ${className}`}
      disabled={disabled}
      onClick={onClick}
      {...props}
    >
      {children}
    </button>
  );
};

const Card = ({ children, className = '', variant = 'default' }) => {
  const variants = {
    default: 'bg-card border border-border rounded-xl',
    neon: 'bg-neobanka-black-500 border-2 border-neobanka-teal-500 rounded-xl',
    glass: 'bg-neobanka-black-400 border border-neobanka-teal-500/40 rounded-xl',
    solid: 'bg-neobanka-black-300 border-l-4 border-l-neobanka-teal-400 rounded-r-xl',
    minimal: 'bg-transparent border-t-2 border-t-neobanka-teal-500 pt-4',
    terminal: 'bg-neobanka-black-800 border border-neobanka-teal-600 rounded-lg font-mono'
  };

  return (
    <div className={`${variants[variant]} ${className}`}>
      {children}
    </div>
  );
};

const CardHeader = ({ children, className = '' }) => (
  <div className={`p-4 border-b border-border ${className}`}>{children}</div>
);

const CardTitle = ({ children, className = '' }) => (
  <h3 className={`text-lg font-semibold text-foreground ${className}`}>{children}</h3>
);

const CardContent = ({ children, className = '' }) => (
  <div className={`p-4 ${className}`}>{children}</div>
);

const Badge = ({ children, variant = 'default', className = '' }) => {
  const variants = {
    default: 'bg-primary text-primary-foreground',
    success: 'bg-green-500 text-white',
    destructive: 'bg-red-500 text-white',
    secondary: 'bg-secondary text-secondary-foreground',
    outline: 'border border-border text-foreground'
  };

  return (
    <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${variants[variant]} ${className}`}>
      {children}
    </span>
  );
};

const Input = ({ className = '', ...props }) => (
  <input
    className={`flex h-9 w-full rounded-md border border-border bg-background px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    {...props}
  />
);

// const Label = ({ children, className = '', ...props }) => (
//   <label className={`text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 ${className}`} {...props}>
//     {children}
//   </label>
// );

// Trading Components
const OrderBookPanel = ({ symbol, orderbook, onRefresh, loading, fromNetwork, toNetwork }) => {
  return (
    <Card variant="glass" className="h-full">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3 border-b border-neobanka-teal-500/30">
        <CardTitle className="text-base font-semibold text-white">
          Order Book
        </CardTitle>
        <div className="flex items-center space-x-2">
          <div className="px-2 py-1 bg-neobanka-teal-500/20 border border-neobanka-teal-400 rounded text-xs font-mono text-neobanka-teal-300">
            {symbol}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefresh}
            disabled={loading}
            className="p-2 hover:bg-neobanka-teal-500/20 rounded"
          >
            <RefreshCw className={`h-4 w-4 text-neobanka-teal-400 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="pt-2">
        <div className="space-y-4">
          {/* Asks */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-red-600 font-medium">Asks</span>
              <span className="text-xs text-muted-foreground">Price / Size / Total / Chain</span>
            </div>
            <div className="space-y-1 max-h-32 overflow-y-auto">
              {orderbook?.asks?.length > 0 ? (
                orderbook.asks.map((ask, index) => {
                  const chain = (ask.from_network && ask.to_network)
                    ? [ask.from_network, ask.to_network].join(' → ')
                    : (fromNetwork && toNetwork ? `${fromNetwork} → ${toNetwork}` : '');
                  return (
                    <div key={index} className="flex justify-between text-xs">
                      <span className="text-red-600 font-medium">{ask.price.toFixed(4)}</span>
                      <span className="text-foreground">{ask.amount.toFixed(2)}</span>
                      <span className="text-muted-foreground">{ask.total.toFixed(2)}</span>
                      <Badge variant="outline" className="ml-2">{chain || '—'}</Badge>
                    </div>
                  );
                })
              ) : (
                <div className="text-xs text-muted-foreground">No asks available</div>
              )}
            </div>
          </div>

          {/* Spread */}
          <div className="border-t border-b border-border py-2">
            <div className="text-center">
              <span className="text-xs text-muted-foreground">
                Spread: {orderbook?.asks?.[0] && orderbook?.bids?.[0]
                  ? (orderbook.asks[0].price - orderbook.bids[0].price).toFixed(4)
                  : 'N/A'
                }
              </span>
            </div>
          </div>

          {/* Bids */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-green-600 font-medium">Bids</span>
              <span className="text-xs text-muted-foreground">Price / Size / Total / Chain</span>
            </div>
            <div className="space-y-1 max-h-32 overflow-y-auto">
              {orderbook?.bids?.length > 0 ? (
                orderbook.bids.map((bid, index) => {
                  const chain = (bid.from_network && bid.to_network)
                    ? [bid.from_network, bid.to_network].join(' → ')
                    : (fromNetwork && toNetwork ? `${fromNetwork} → ${toNetwork}` : '');
                  return (
                    <div key={index} className="flex justify-between text-xs">
                      <span className="text-green-600 font-medium">{bid.price.toFixed(4)}</span>
                      <span className="text-foreground">{bid.amount.toFixed(2)}</span>
                      <span className="text-muted-foreground">{bid.total.toFixed(2)}</span>
                      <Badge variant="outline" className="ml-2">{chain || '—'}</Badge>
                    </div>
                  );
                })
              ) : (
                <div className="text-xs text-muted-foreground">No bids available</div>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

const TradingPanel = ({ account, onOrderSubmit, loading, fromNetwork, toNetwork, setFromNetwork, setToNetwork, onSymbolChange, variant = 'same' }) => {
  const [orderType, setOrderType] = useState('limit');
  const [side, setSide] = useState('buy');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('');
  const [baseAsset, setBaseAsset] = useState('HBAR');
  const [quoteAsset, setQuoteAsset] = useState('USDT');
  const [marketPrice, setMarketPrice] = useState<PriceData | null>(null);
  const [autoUpdatePrice, setAutoUpdatePrice] = useState(true);
  const [receiveWallet, setReceiveWallet] = useState<string>(account || '');
  const [isSwitching, setIsSwitching] = useState(false);

  // Quick toggle: switch MetaMask to the selected from-network (where approvals/deposits happen)
  const switchOrAddNetwork = (() => {
    let inFlight = false;
    return async (targetKey: 'hedera' | 'ethereum') => {
      if (inFlight) return; // prevent concurrent switches
      const eth: any = (window as any).ethereum;
      if (!eth || !eth.request) return;
      try {
        inFlight = true;
        setIsSwitching(true);
        const target = CHAIN_REGISTRY[targetKey];
        const hexChainId = '0x' + target.chainId.toString(16);
        try {
          await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: hexChainId }] });
        } catch (switchErr: any) {
          const needsAdd = switchErr?.code === 4902 || /Unrecognized chain ID/i.test(String(switchErr?.message || ''));
          if (!needsAdd) throw switchErr;
          const params = targetKey === 'hedera'
            ? {
              chainId: '0x' + CHAIN_REGISTRY.hedera.chainId.toString(16),
              chainName: HEDERA_TESTNET.chainName,
              nativeCurrency: HEDERA_TESTNET.nativeCurrency as any,
              rpcUrls: (HEDERA_TESTNET.rpcUrls as any) || [],
              blockExplorerUrls: HEDERA_TESTNET.blockExplorerUrls as any,
            }
            : {
              chainId: '0x' + CHAIN_REGISTRY.ethereum.chainId.toString(16),
              chainName: 'Ethereum Sepolia',
              nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },
              rpcUrls: [CHAIN_REGISTRY.ethereum.rpc].filter(Boolean),
              blockExplorerUrls: ['https://sepolia.etherscan.io'] as any,
            };
          await eth.request({ method: 'wallet_addEthereumChain', params: [params] });
          await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: params.chainId }] });
        }
        // Verify the network actually switched (wallet UIs can lag)
        const anyWindow: any = window as any;
        const provider = anyWindow?.ethereum ? new ethers.BrowserProvider(anyWindow.ethereum) : null;
        if (provider) {
          for (let i = 0; i < 5; i++) {
            try {
              const net = await provider.getNetwork();
              if (Number(net.chainId) === CHAIN_REGISTRY[targetKey].chainId) break;
            } catch { }
            await new Promise(r => setTimeout(r, 500));
          }
        }
      } finally {
        setIsSwitching(false);
        inFlight = false;
      }
    };
  })();

  const isQuickActive = (from: string, to: string) => (fromNetwork === from && toNetwork === to);

  // Subscribe to price updates
  useEffect(() => {
    const symbol = `${baseAsset}_${quoteAsset}`;

    // notify parent symbol selection
    try { onSymbolChange && onSymbolChange(symbol); } catch { }

    const unsubscribe = priceService.subscribe(
      symbol,
      (priceData) => {
        setMarketPrice(priceData);

        // Auto-update price field when pair changes or if enabled and empty
        if (autoUpdatePrice) {
          setPrice(PriceService.formatPrice(priceData.price));
        }
      },
      30000 // Update every 30 seconds
    );

    return () => {
      unsubscribe();
    };
  }, [baseAsset, quoteAsset, autoUpdatePrice]);

  // Auto-fill price when trading pair changes
  useEffect(() => {
    if (marketPrice && autoUpdatePrice) {
      setPrice(PriceService.formatPrice(marketPrice.price));
    }
  }, [baseAsset, quoteAsset]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!quantity || !account) return;
    const priceToUse = orderType === 'market'
      ? (marketPrice ? PriceService.formatPrice(marketPrice.price) : price || '0')
      : price;
    if (!priceToUse) return;

    try {
      // Debug: log chain + settlement + token addresses being submitted
      try {
        const escrowNetKey = String(fromNetwork || '').toLowerCase();
        const settlementAddr = resolveSettlementAddress(escrowNetKey as 'hedera' | 'ethereum');
        const baseAddr = resolveTokenAddress(escrowNetKey as 'hedera' | 'ethereum', baseAsset);
        const quoteAddr = resolveTokenAddress(escrowNetKey as 'hedera' | 'ethereum', quoteAsset);
        console.log('Submit (ui) — outbound payload preview', {
          escrowNetKey,
          chainId: CHAIN_REGISTRY[escrowNetKey as 'hedera' | 'ethereum']?.chainId,
          settlementAddr,
          baseAsset,
          baseAddr,
          quoteAsset,
          quoteAddr,
          fromNetwork,
          toNetwork,
          side,
          price: priceToUse,
          quantity,
        });
      } catch { }

      await onOrderSubmit({
        account,
        baseAsset,
        quoteAsset,
        price: parseFloat(priceToUse),
        quantity: parseFloat(quantity),
        side: side === 'buy' ? 'bid' : 'ask',
        fromNetwork,
        toNetwork,
        receiveWallet: receiveWallet || account,
        type: orderType as any,
      });

      // Keep inputs to allow rapid subsequent orders; user can edit as needed
    } catch (error) {
      console.error('Order submission failed:', error);
      // Error handling should be done in the parent component
    }
  };

  // Auto-switch to the chosen from-network when user changes it via select
  useEffect(() => {
    const key = (fromNetwork || '').toLowerCase();
    if (key === 'hedera' || key === 'ethereum') {
      // fire-and-forget; guarded in switch function
      switchOrAddNetwork(key as 'hedera' | 'ethereum').catch(() => { });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromNetwork]);

  return (
    <Card variant="neon" className="h-full">
      <CardHeader className="border-b border-neobanka-teal-500/20">
        <CardTitle className="text-base flex items-center gap-2">
          <div className="w-2 h-2 bg-neobanka-teal-400 rounded-full"></div>
          Place Order
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-3 gap-2">
            <Button type="button" variant={orderType === 'limit' ? 'default' : 'outline'} size="sm" onClick={() => setOrderType('limit')}>Limit</Button>
            <Button type="button" variant={orderType === 'market' ? 'default' : 'outline'} size="sm" onClick={() => setOrderType('market')}>Market</Button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant={side === 'buy' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSide('buy')}
              className={side === 'buy' ? 'bg-green-600 hover:bg-green-700' : ''}
            >
              Buy
            </Button>
            <Button
              type="button"
              variant={side === 'sell' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSide('sell')}
              className={side === 'sell' ? 'bg-red-600 hover:bg-red-700' : ''}
            >
              Sell
            </Button>
          </div>

          {/* Cross-chain leg quick toggles (cross variant only) */}
          {variant === 'cross' && (
            <div className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                variant={isQuickActive('hedera', 'ethereum') ? 'default' : 'outline'}
                size="sm"
                disabled={isSwitching}
                onClick={async () => { setFromNetwork('hedera'); setToNetwork('ethereum'); try { await switchOrAddNetwork('hedera'); } catch (err) { console.error('Error switching to Hedera:', err); } }}
              >
                Hedera → Sepolia
              </Button>
              <Button
                type="button"
                variant={isQuickActive('ethereum', 'hedera') ? 'default' : 'outline'}
                size="sm"
                disabled={isSwitching}
                onClick={async () => { setFromNetwork('ethereum'); setToNetwork('hedera'); try { await switchOrAddNetwork('ethereum'); } catch { } }}
              >
                Sepolia → Hedera
              </Button>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <NetworkList
              network={fromNetwork}
              setNetwork={setFromNetwork}
              label={"From network"}
              assetList={variant === 'cross' ? ["hedera", "ethereum"] : ["hedera"]}
            />
            <NetworkList
              network={toNetwork}
              setNetwork={setToNetwork}
              label={"To Network"}
              assetList={variant === 'cross' ? ["hedera", "ethereum"] : ["hedera"]}
            />

          </div>

          {variant === 'cross' && (
            <div>
              <LabelTerminal htmlFor="receiveWallet" className="text-sm">Receive Wallet on To Network (optional)</LabelTerminal>
              <Input
                id="receiveWallet"
                type="text"
                value={receiveWallet}
                onChange={(e) => setReceiveWallet(e.target.value)}
                placeholder={account || '0x...'}
              />
              <div className="text-[11px] text-muted-foreground mt-1">If empty, your wallet address will be used on the destination chain.</div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <AssetList asset={baseAsset} setAsset={setBaseAsset} label={"Base Asset"} assetList={["HBAR", "xZAR", "cNGN"]} />
            <AssetList asset={quoteAsset} setAsset={setQuoteAsset} label={"Quote Asset"} assetList={["USDT", "xZAR", "cNGN", "HBAR"]} />

          </div>

          <div>
            <div className="flex justify-between items-center mb-1">
              <LabelTerminal htmlFor="price" className="text-sm">Price</LabelTerminal>
              {orderType === 'market' ? (
                <div className="text-xs text-muted-foreground">Market price will be used</div>
              ) : marketPrice && (
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-muted-foreground">Market:</span>
                  <span className="font-mono font-medium text-primary">
                    {PriceService.formatPrice(marketPrice.price)}
                  </span>
                  <span className={`font-semibold ${marketPrice.change24h >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                    {PriceService.formatChange(marketPrice.change24h)}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setPrice(PriceService.formatPrice(marketPrice.price));
                      setAutoUpdatePrice(!autoUpdatePrice);
                    }}
                    className={`p-1 rounded hover:bg-accent ${autoUpdatePrice ? 'text-primary' : 'text-muted-foreground'}`}
                    title={autoUpdatePrice ? 'Auto-update ON (click to toggle)' : 'Auto-update OFF (click to toggle)'}
                  >
                    <RefreshCw className={`h-3 w-3 ${autoUpdatePrice ? 'animate-pulse' : ''}`} />
                  </button>
                </div>
              )}
            </div>
            <Input
              id="price"
              type="number"
              step="0.0001"
              value={price}
              onChange={(e) => {
                setPrice(e.target.value);
                setAutoUpdatePrice(false); // Disable auto-update when user types
              }}
              placeholder={marketPrice ? PriceService.formatPrice(marketPrice.price) : "0.0000"}
              disabled={orderType === 'market'}
            />
            {marketPrice && (
              <div className="mt-1 grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                <span>Bid: {PriceService.formatPrice(marketPrice.bid)}</span>
                <span>Ask: {PriceService.formatPrice(marketPrice.ask)}</span>
                <span>Vol: {(marketPrice.volume24h / 1000000).toFixed(2)}M</span>
              </div>
            )}
          </div>

          <div>
            <LabelTerminal htmlFor="quantity" className="text-sm">Quantity</LabelTerminal>
            <Input
              id="quantity"
              type="number"
              step="0.01"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="0.00"
            />
          </div>

          {price && quantity && (
            <div className="text-xs text-muted-foreground">
              Total: {(parseFloat(price) * parseFloat(quantity)).toFixed(4)} {quoteAsset}
            </div>
          )}

          <Button
            type="submit"
            className="w-full"
            disabled={loading || !account || !price || !quantity}
            variant={side === 'buy' ? 'success' : 'destructive'}
            onClick={() => { }}
          >
            {loading ? (
              <div className="flex items-center space-x-2">
                <RefreshCw className="h-4 w-4 animate-spin" />
                <span>Submitting...</span>
              </div>
            ) : (
              <div className="flex items-center space-x-2">
                {side === 'buy' ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
                <span>{`${side.charAt(0).toUpperCase() + side.slice(1)} ${baseAsset}`}</span>
              </div>
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
};

// (MCP/Agents panel removed)

const TerminalLog = ({ logs }) => {
  const logRef = useRef(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <Card variant="terminal" className="h-full">
      <CardHeader className="pb-2 border-b border-neobanka-teal-600/20">
        <CardTitle className="text-base flex items-center gap-3 font-mono text-neobanka-teal-300">
          <div className="flex items-center gap-1">
            <div className="w-3 h-3 bg-neobanka-teal-400 rounded-full"></div>
            <div className="w-3 h-3 bg-neobanka-teal-600 rounded-full"></div>
            <div className="w-3 h-3 bg-neobanka-teal-800 rounded-full"></div>
          </div>
          <Terminal className="h-4 w-4" />
          ACTIVITY_LOG.sh
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-3">
        <div ref={logRef} className="h-48 overflow-y-auto bg-neobanka-black-900 border border-neobanka-teal-700 rounded p-4 text-xs font-mono">
          <div className="text-neobanka-teal-400 mb-2 text-[10px]">$ tail -f /var/log/neobanka/activity.log</div>
          {logs.map((log, index) => (
            <div key={index} className={`mb-1 flex items-start gap-2 ${log.type === 'error' ? 'text-red-400' : log.type === 'success' ? 'text-green-400' : 'text-gray-300'}`}>
              <span className="text-neobanka-teal-600 shrink-0 w-16 text-[10px]">[{log.timestamp}]</span>
              <span className="leading-tight">{log.message}</span>
            </div>
          ))}
          <div className="flex items-center gap-1 mt-2 text-neobanka-teal-400 text-[10px]">
            <span>$</span>
            <div className="w-2 h-3 bg-neobanka-teal-400"></div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

const OrderHistoryPanel = ({ symbol, useCross = false }) => {
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      try {
        const res = useCross
          ? await orderbookApi.getOrderHistoryCross(symbol, 100)
          : await orderbookApi.getOrderHistory(symbol, 100);
        if (mounted) {
          const hist = Array.isArray(res?.history) ? res.history : [];
          // Normalize timestamps and sort
          const normalized = hist.map(item => ({
            ...item,
            // Normalize to milliseconds - if timestamp is too small, it's in seconds
            normalizedTimestamp: item.timestamp < 10000000000
              ? item.timestamp * 1000
              : item.timestamp
          }));
          normalized.sort((a, b) => b.normalizedTimestamp - a.normalizedTimestamp);
          setRows(normalized);
        }
      } catch { }
      setLoading(false);
    };
    load();
    const id = setInterval(load, 10000);
    return () => { mounted = false; clearInterval(id); };
  }, [symbol, useCross]);

  return (
    <Card variant="minimal" className="h-full">
      <CardHeader className="pb-3 px-0">
        <CardTitle className="text-base flex items-center gap-2 text-white">
          <BookOpen className="h-4 w-4 text-neobanka-teal-400" />
          Order History
          <div className="ml-auto text-xs text-neobanka-teal-500 font-mono">{rows.length} records</div>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-2">
        <div className="h-48 overflow-y-auto text-xs">
          {rows.length === 0 && !loading && (
            <div className="text-muted-foreground">No history yet</div>
          )}
          {rows.map((r, i) => {
            const short = (h?: string) => h ? `${h.substring(0, 10)}…${h.substring(h.length - 6)}` : '';
            const hasTx = !!(r.txHash || r.txHashSource || r.txHashDestination);
            // Use normalized timestamp for display
            const displayTime = new Date(r.normalizedTimestamp || r.timestamp || Date.now());

            return (
              <div key={i} className="flex justify-between items-center py-1 border-b border-border/40 gap-2">
                <span className="text-muted-foreground">{displayTime.toLocaleTimeString()}</span>
                <span>{r.type}</span>
                <span>{r.symbol}</span>
                <span>{r.side || ''}</span>
                <span>{r.price ? Number(r.price).toFixed(4) : ''}</span>
                <span>{r.quantity || ''}</span>
                {hasTx && (
                  <span className="text-primary font-mono">
                    {r.txHash ? (
                      <a href={`https://hashscan.io/testnet/transaction/${r.txHash}`} target="_blank" rel="noreferrer">{short(r.txHash)}</a>
                    ) : (
                      <span className="flex gap-1">
                        {r.txHashSource && r.txHashDestination && r.txHashSourceChainName == "hedera" && r.txHashDestChainName == "ethereum" ? (
                          <a href={`https://hashscan.io/testnet/transaction/0x${r.txHashSource}`} target="_blank" rel="noreferrer">src:{short(r.txHashSource)}</a>
                        ) : (<a href={`https://sepolia.etherscan.io/tx/0x${r.txHashSource}`} target="_blank" rel="noreferrer">src:{short(r.txHashSource)}</a>)}
                        {r.txHashSource && r.txHashDestination && r.txHashDestChainName == "hedera" && r.txHashSourceChainName == "ethereum" ? (
                          <a href={`https://hashscan.io/testnet/transaction/0x${r.txHashDestination}`} target="_blank" rel="noreferrer">dst:{short(r.txHashDestination)}</a>
                        ) : (<a href={`https://sepolia.etherscan.io/tx/0x${r.txHashDestination}`} target="_blank" rel="noreferrer">dst:{short(r.txHashDestination)}</a>)}
                      </span>
                    )}
                  </span>
                )}
              </div>
            );
          })}
          {loading && <div className="text-muted-foreground">Loading…</div>}
        </div>
      </CardContent>
    </Card>
  );
};

const MarketStats = ({ symbol, orderbook }) => {
  const stats = {
    lastPrice: orderbook?.asks?.[0]?.price || orderbook?.bids?.[0]?.price || 0,
    change24h: '+5.23%',
    volume24h: '2.4M',
    high24h: '0.3456',
    low24h: '0.3123'
  };

  return (
    <Card variant="solid" className="h-full">
      <CardHeader className="pb-3 border-b-0">
        <CardTitle className="text-base flex items-center gap-2 text-neobanka-teal-300">
          <div className="p-1.5 bg-neobanka-teal-500/20 rounded-lg">
            <BarChart3 className="h-4 w-4 text-neobanka-teal-400" />
          </div>
          Market Statistics
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-2">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <div className="text-xs text-muted-foreground">Last Price</div>
            <div className="text-lg font-semibold text-foreground">{stats.lastPrice.toFixed(4)}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">24H Change</div>
            <div className="text-lg font-semibold text-green-600">{stats.change24h}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">24H Volume</div>
            <div className="text-lg font-semibold text-foreground">{stats.volume24h}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">24H Range</div>
            <div className="text-sm font-medium text-foreground">{stats.low24h} - {stats.high24h}</div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

const PriceChartPanel = ({ symbol }) => {
  const [series, setSeries] = useState<{ t: number; p: number }[]>([]);

  useEffect(() => {
    // reset on symbol change
    setSeries([]);
    const unsub = priceService.subscribe(symbol, (pd) => {
      setSeries((prev) => {
        const next = [...prev, { t: pd.timestamp, p: pd.price }];
        return next.slice(-120);
      });
    }, 10000);
    return () => unsub();
  }, [symbol]);

  const data = series.map(pt => ({ time: pt.t, price: pt.p }));

  return (
    <Card variant="glass" className="h-full">
      <CardHeader className="pb-3 border-b border-neobanka-teal-500/30">
        <CardTitle className="text-base flex items-center gap-2">
          <div className="w-8 h-8 bg-neobanka-teal-500 rounded-xl flex items-center justify-center">
            <TrendingUp className="h-4 w-4 text-white" />
          </div>
          <span className="text-white">
            Price Chart ({symbol})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-2">
        <div style={{ height: 260 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2a2a" />
              <XAxis dataKey="time" tickFormatter={(v) => new Date(v).toLocaleTimeString()} stroke="#9aa0a6" />
              <YAxis domain={["auto", "auto"]} stroke="#9aa0a6" />
              <Tooltip />
              <Line type="monotone" dataKey="price" stroke="#60a5fa" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
};

// Main Trading Terminal Component
export function TradingTerminal({ onSymbolChange, variant = 'same', symbolSuffix = '', defaultFromNetwork, defaultToNetwork, useTradeImpl }: { onSymbolChange?: (s: string) => void; variant?: 'same' | 'cross'; symbolSuffix?: string; defaultFromNetwork?: string; defaultToNetwork?: string; useTradeImpl?: () => any; }) {
  const {
    account,
    isConnected,
    isConnecting,
    connect,
    disconnect,
    isOnHederaTestnet,
    switchToHederaTestnet
  } = useWallet();
  const [fromNetwork, setFromNetwork] = useState(defaultFromNetwork || 'hedera');
  const [toNetwork, setToNetwork] = useState(defaultToNetwork || 'hedera');

  // Initialize trade hook (injectable for separation of concerns)
  const trade = (typeof useTradeImpl === 'function' ? useTradeImpl() : useTrade());
  const orderStatus = trade.orderStatus;
  const tradeLoading = trade.loading;
  const { toast } = useToast();

  const [orderbook, setOrderbook] = useState(null);
  const [loading, setLoading] = useState(false);
  // (Agents state removed)
  const [currentMarketPrice, setCurrentMarketPrice] = useState<PriceData | null>(null);
  const [allMarketPrices, setAllMarketPrices] = useState<Record<string, PriceData>>({});
  const [logs, setLogs] = useState([
    { timestamp: new Date().toLocaleTimeString(), message: 'System initialized', type: 'info' },
    { timestamp: new Date().toLocaleTimeString(), message: 'Waiting for wallet connection...', type: 'info' }
  ]);
  // (Agent status removed)

  const [currentSymbol, setCurrentSymbol] = useState('HBAR_USDT');
  const bookSymbol = symbolSuffix ? `${currentSymbol}${symbolSuffix}` : currentSymbol;

  useEffect(() => {
    try { onSymbolChange && onSymbolChange(currentSymbol); } catch { }
  }, [currentSymbol, onSymbolChange]);

  const addLog = (message, type = 'info') => {
    const newLog = {
      timestamp: new Date().toLocaleTimeString(),
      message,
      type
    };
    setLogs(prev => [...prev.slice(-49), newLog]);
  };

  const loadOrderbook = async () => {
    if (!isConnected) return; // Don't load if not connected

    setLoading(true);
    try {
      const response = variant === 'cross'
        ? await orderbookApi.getOrderbookCross(bookSymbol, fromNetwork, toNetwork)
        : await orderbookApi.getOrderbook(bookSymbol, fromNetwork, toNetwork);
      if (response.status_code === 1) {
        setOrderbook(response.orderbook);
        addLog(`Orderbook loaded: ${response.orderbook.asks?.length || 0} asks, ${response.orderbook.bids?.length || 0} bids`);
      } else {
        addLog(`Failed to load orderbook: ${response.message || 'Unknown error'}`, 'error');
      }
    } catch (error: any) {
      console.error('Orderbook error:', error);

      // Handle specific error types
      if (error.name === 'TypeError' && error.message.includes('Failed to fetch')) {
        addLog('Unable to connect to orderbook server. Please check your connection.', 'error');
      } else if (error.message.includes('CORS')) {
        addLog('Cross-origin request blocked. Please check server configuration.', 'error');
      } else {
        addLog(`Orderbook error: ${error.message}`, 'error');
      }

      // Clear stale orderbook data
      setOrderbook(null);
    } finally {
      setLoading(false);
    }
  };

  const lastStatusRef = useRef<string>('idle');

  // Toast once per status change
  useEffect(() => {
    if (!orderStatus || orderStatus === lastStatusRef.current) return;
    lastStatusRef.current = orderStatus;
    if (orderStatus === 'approving') {
      addLog('Waiting for token approval...', 'info');
      toast({ title: 'Approval', description: 'Waiting for token approval…' });
    } else if (orderStatus === 'depositing') {
      addLog('Depositing funds to escrow...', 'info');
      toast({ title: 'Escrow', description: 'Depositing to escrow…' });
    } else if (orderStatus === 'submitting') {
      addLog('Submitting order to orderbook...', 'info');
      toast({ title: 'Order', description: 'Submitting order…' });
    } else if (orderStatus === 'completed') {
      toast({ title: 'Success', description: 'Order submitted.', variant: 'success' as any });
    } else if (orderStatus === 'failed') {
      toast({ title: 'Error', description: 'Order failed.', variant: 'destructive' as any });
    }
  }, [orderStatus]);

  const handleOrderSubmit = async (orderData) => {
    try {
      // First check wallet connection
      if (!account) {
        addLog('Please connect your wallet first', 'error');
        return;
      }

      // Log the order details
      addLog(`Preparing ${orderData.side} order: ${orderData.quantity} ${orderData.baseAsset} @ ${orderData.price}`);

      // Update log based on order status
      // no interval-based toasts; handled by orderStatus effect above

      // Submit the order (hook handles all prerequisites)
      const submitOrderFn = variant === 'cross'
        ? (trade.submitOrderCross || trade.submitOrder)
        : trade.submitOrder;
      const result = await submitOrderFn({ ...orderData });

      // nothing

      if (result.success) {
        addLog(`Order submitted successfully. ID: ${result.orderId}`, 'success');
        toast({ title: 'Order submitted', description: `ID: ${result.orderId}`, variant: 'success' as any });
        if (result.trades?.length > 0) {
          addLog(`Order matched! ${result.trades.length} trades executed`, 'success');
          result.trades.forEach((trade, idx) => {
            addLog(`Trade ${idx + 1}: ${trade.quantity} @ ${trade.price}`, 'success');
          });
        }
        // Refresh orderbook after successful order
        await loadOrderbook();
      } else {
        addLog(`Order failed`, 'error');
        toast({ title: 'Order failed', description: 'Settlement did not complete.', variant: 'destructive' as any });
      }
    } catch (error: any) {
      console.error('Order error:', error);
      let errorMsg = typeof error?.message === 'string' ? error.message : 'Unknown error occurred';
      // Extra: decode Hedera hex-ASCII messages in the UI as fallback
      const dataHex = error?.data || error?.error?.data || error?.value?.data || error?.info?.error?.data;
      if (typeof dataHex === 'string' && dataHex.startsWith('0x')) {
        try {
          const clean = dataHex.slice(2);
          const bytes = clean.match(/.{1,2}/g) || [];
          const text = bytes.map(b => String.fromCharCode(parseInt(b, 16))).join('');
          if (/[A-Z_]/.test(text)) errorMsg = text;
        } catch { }
      }
      addLog(`Order error: ${errorMsg}`, 'error');
      toast({ title: 'Error', description: errorMsg, variant: 'destructive' as any });

      // Provide user-friendly error messages
      if (errorMsg.includes('insufficient')) {
        addLog('Please deposit more tokens to your wallet or escrow', 'info');
      } else if (errorMsg.includes('allowance')) {
        addLog('Please approve token spending to settlement contract', 'info');
      } else if (errorMsg.includes('user rejected')) {
        addLog('Transaction was rejected in wallet', 'info');
      }
    } finally {
      // Loading indicator for submit button is driven by tradeLoading from useTrade
    }
  };

  // (MCP start removed)

  const formatAddress = (address) => {
    return `${address.slice(0, 6)}...${address.slice(-4)}`;
  };

  // Auto-refresh orderbook
  useEffect(() => {
    if (isConnected) {
      loadOrderbook();
      const interval = setInterval(loadOrderbook, 10000);
      return () => clearInterval(interval);
    }
  }, [isConnected]);

  // Add connection logs
  useEffect(() => {
    if (isConnected) {
      addLog(`Wallet connected: ${formatAddress(account)}`, 'success');
      try {
        const hed = resolveSettlementAddress('hedera');
        if (hed) addLog(`Hedera settlement ${formatAddress(hed)}`, 'success');
        const eth = resolveSettlementAddress('ethereum');
        if (eth) addLog(`Sepolia settlement ${formatAddress(eth)}`, 'success');
      } catch { }
    }
  }, [isConnected, account]);


  // Subscribe to market prices for all supported pairs
  useEffect(() => {
    const supportedPairs = ['HBAR_USDT', 'xZAR_USDT', 'cNGN_USDT'];
    const unsubscribeFunctions: (() => void)[] = [];
    let firstUpdate = true;

    supportedPairs.forEach(pair => {
      const unsubscribe = priceService.subscribe(
        pair,
        (priceData) => {
          // Update individual price
          setAllMarketPrices(prev => ({ ...prev, [pair]: priceData }));

          // Set main market price (default to HBAR_USDT)
          if (pair === 'HBAR_USDT') {
            setCurrentMarketPrice(priceData);
            if (firstUpdate) {
              addLog(`Market price feeds connected for ${supportedPairs.length} pairs`, 'success');
              firstUpdate = false;
            }
          }
        },
        30000 // Update every 30 seconds
      );
      unsubscribeFunctions.push(unsubscribe);
    });

    return () => {
      unsubscribeFunctions.forEach(fn => fn());
    };
  }, []);

  return (
    <div className="space-y-6">


      {/* Connection Status - Only show alerts for issues */}
      {!isConnected && (
        <div className="bg-destructive/10 border border-destructive/20 rounded-lg p-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-destructive" />
            <span className="text-sm font-medium">Wallet Not Connected</span>
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Please connect your wallet to access trading features and contribute to impact projects.
          </p>
        </div>
      )}

      {variant === 'same' && isConnected && !isOnHederaTestnet && (
        <div className="bg-destructive/10 border border-destructive/20 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-destructive" />
              <span className="text-sm font-medium">Wrong Network</span>
            </div>
            <Button
              onClick={switchToHederaTestnet}
              variant="destructive"
              size="sm"
              className="flex items-center gap-2"
            >
              Switch to Hedera Testnet
            </Button>
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Please switch to Hedera Testnet to access trading features.
          </p>
        </div>
      )}

      {/* Market Stats Bar */}
      {Object.keys(allMarketPrices).length > 0 && (
        <div className="bg-neobanka-black-400 border-2 border-neobanka-teal-500 rounded-xl p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-8">
              <div className="flex items-center space-x-3">
                <div className="w-10 h-10 bg-neobanka-teal-500 rounded-xl flex items-center justify-center">
                  <BarChart3 className="h-5 w-5 text-white" />
                </div>
                <div>
                  <div className="text-sm font-bold text-neobanka-teal-300">Live Markets</div>
                  <div className="text-xs text-gray-400">Spot Trading</div>
                </div>
              </div>

              {/* Display all trading pairs */}
              <div className="flex items-center space-x-8">
                {Object.entries(allMarketPrices).map(([symbol, priceData]) => (
                  <div key={symbol} className="text-center">
                    <div className="text-xs text-gray-400 mb-1">{symbol.replace('_', '/')}</div>
                    <div className="font-mono font-bold text-lg text-white">
                      ${PriceService.formatPrice(priceData.price)}
                    </div>
                    <div className={`text-xs font-semibold ${priceData.change24h >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {PriceService.formatChange(priceData.change24h)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="flex items-center space-x-2 px-3 py-2 bg-neobanka-teal-500/20 border border-neobanka-teal-500 rounded">
              <div className="w-2 h-2 bg-neobanka-teal-400 rounded-full"></div>
              <span className="text-xs font-medium text-neobanka-teal-300">LIVE</span>
            </div>
          </div>
        </div>
      )}

      {/* Balances Row */}
      <div className="grid grid-cols-1">
        <BalancePanel />
      </div>

      {/* Main Trading Interface */}
      <div className="space-y-6">

        {/* Top Row - Charts and Trading Panel */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Candles Chart Section */}
          <div className="bg-neobanka-black-400 border border-neobanka-teal-500 rounded-xl p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 bg-neobanka-teal-500 rounded-lg flex items-center justify-center">
                  <BarChart3 className="h-4 w-4 text-white" />
                </div>
                <div>
                  <div className="text-sm font-bold text-white">Candles</div>
                  <div className="text-xs text-neobanka-teal-400 font-mono">{currentSymbol}</div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 bg-green-400 rounded-full"></div>
              </div>
            </div>

            {/* Chart Content */}
            <div className="h-[300px]">
              <TradingViewChart
                symbol={currentSymbol}
                onSymbolChange={setCurrentSymbol}
                className="h-full"
              />
            </div>
          </div>

          {/* Trading Panel */}
          <TradingPanel
            account={account}
            onOrderSubmit={handleOrderSubmit}
            loading={tradeLoading}
            fromNetwork={fromNetwork}
            toNetwork={toNetwork}
            setFromNetwork={setFromNetwork}
            setToNetwork={setToNetwork}
            onSymbolChange={setCurrentSymbol}
            variant={variant}
          />
        </div>

        {/* Chart under Place Order */}
        {/* <div className="grid grid-cols-1 gap-6">
          <PriceChartPanel symbol={currentSymbol} />
        </div> */}

        {/* Middle Row - Order Book and Market Stats */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <OrderBookPanel
            symbol={bookSymbol}
            orderbook={orderbook}
            onRefresh={loadOrderbook}
            loading={loading}
            fromNetwork={fromNetwork}
            toNetwork={toNetwork}
          />
          <OrderHistoryPanel symbol={bookSymbol} useCross={variant === 'cross'} />
        </div>

        {/* Bottom Row - Activity Log */}
        <div className="grid grid-cols-1 gap-6">
          <TerminalLog logs={logs} />
        </div>

        {/* Quick Actions */}
        <div className="flex items-center justify-between border-t border-border pt-6">
          <div className="flex items-center space-x-4">
            <Button
              variant="outline"
              size="sm"
              onClick={loadOrderbook}
              disabled={loading}
              className="text-sm"
            >
              <RefreshCw className="h-4 w-4 mr-2" />
              Refresh Data
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => addLog('Settings accessed')}
              className="text-sm"
            >
              <Settings className="h-4 w-4 mr-2" />
              Settings
            </Button>
          </div>
          <div className="flex items-center space-x-2 text-sm text-muted-foreground">
            <div className={`w-2 h-2 rounded-full ${isConnected && isOnHederaTestnet ? 'bg-green-500' : 'bg-red-500'}`}></div>
            <span>System {isConnected && isOnHederaTestnet ? 'Active' : 'Standby'}</span>
          </div>
        </div>
      </div>

      {/* Status Footer */}
      <div className="bg-gradient-to-r from-neobanka-blue-50/50 to-neobanka-gold-50/50 border border-neobanka-blue-200 rounded-xl p-4 mt-6 shadow-sm">
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center space-x-6">
            <div className="flex items-center space-x-2">
              <div className={`w-3 h-3 rounded-full shadow-sm ${isConnected && isOnHederaTestnet ? 'bg-neobanka-success shadow-neobanka-success/20' : 'bg-neobanka-error shadow-neobanka-error/20'}`}></div>
              <span className="text-muted-foreground">Network:</span>
              <span className="font-semibold text-neobanka-blue-700">{isOnHederaTestnet ? 'Hedera Testnet' : 'Disconnected'}</span>
            </div>
          </div>
          <div className="flex items-center space-x-6">
            <div className="flex items-center space-x-2">
              <span className="text-muted-foreground">Active Orders:</span>
              <span className="font-semibold text-neobanka-blue-700">{orderbook ? (orderbook.asks?.length || 0) + (orderbook.bids?.length || 0) : 0}</span>
            </div>
            <div className="flex items-center space-x-2">
              <span className="text-muted-foreground">Latency:</span>
              <span className="font-mono font-semibold text-neobanka-gold-600">{isConnected ? '47ms' : '—'}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}