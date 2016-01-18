"""
PriceSource module for atxcf-bot. To add price sources, simply extend the PriceSource class
and add an instance of it to the _sources dict.
- transfix@sublevels.net - 20160117
"""

import bitfinex
import poloniex
# TODO: add bittrex

import requests
import re
from pyquery import PyQuery as pq
import unicodedata

import math
import threading

import locale
locale.setlocale(locale.LC_ALL, 'en_US.UTF8')


class PriceSourceError(RuntimeError):
    pass


class PriceSource(object):
    """
    The basic asset price querying interface.
    """

    def get_symbols(self):
        """
        Returns list of asset/currency symbols tradable at this exchange.
        """
        raise NotImplementedError("get_symbols not implemented!")

    def get_base_symbols(self):
        """
        Returns list of base currency symbols used. For instance, in the
        trade pair XBT/USD, the base symbol is USD.
        """
        raise NotImplementedError("get_base_symbols not implemented!")

    def get_price(self, from_asset, to_asset, amount=1.0):
        """
        Returns how much of to_asset you would have after exchanging it
        for amount of from_asset based on the last price traded here.
        """
        raise NotImplementedError("get_price not implemented!")


class Bitfinex(PriceSource):
    """
    Bitfinex exchange interface for atxcf-bot
    """

    def __init__(self):
        self.bfx = bitfinex.Client()
        self.bfx_symbols = self.bfx.symbols()
    

    def get_symbols(self):
        """
        List of symbols at Bitfinex
        """
        s = self.bfx_symbols
        ss = list(set([i[:3] for i in s] + [i[3:] for i in s]))
        return [i.upper() for i in ss]


    def get_base_symbols(self):
        """
        List of base currencies
        """
        return ["USD", "BTC"]


    def get_price(self, from_asset, to_asset, amount=1.0):
        """
        Returns how much of to_asset you would have after exchanging it
        for amount of from_asset based on the last price traded here.        
        """
        symbols = self.get_symbols()
        from_asset = from_asset.upper()
        to_asset = to_asset.upper()
        if not from_asset in symbols:
            raise PriceSourceError("No such symbol %s" % from_asset)
        if not to_asset in symbols:
            raise PriceSourceError("No such symbol %s" % to_asset)

        if from_asset == to_asset:
            return amount

        inverse = False
        from_asset_lower = from_asset.lower()
        to_asset_lower = to_asset.lower()

        bfx_symbol = from_asset_lower + to_asset_lower
        if not bfx_symbol in self.bfx_symbols:
            inverse = True
            bfx_symbol = to_asset_lower + from_asset_lower
            if not bfx_symbol in self.bfx_symbols:
                raise PriceSourceError("Missing market")

        price = float(self.bfx.ticker(bfx_symbol)["last_price"])
        if inverse:
            try:
                price = 1.0/price
            except ZeroDivisionError:
                pass
        return price * amount


class Poloniex(PriceSource):
    """
    Poloniex exchange interface for atxcf-bot
    """

    def __init__(self, creds = "poloniex_cred.json"):
        self.pol = poloniex.poloniex(creds)
        self.pol_ticker = self.pol.returnTicker()
        self._lock = threading.RLock()


    def get_symbols(self):
        """
        List of tradable symbols at Poloniex
        """
        symbol_set = set()
        with self._lock:
            for cur in self.pol_ticker.iterkeys():
                for item in cur.split("_"):
                    symbol_set.add(item)
        return list(symbol_set)


    def get_base_symbols(self):
        """
        List of base currencies at Poloniex
        """
        symbol_set = set()
        with self._lock:
            for cur in self.pol_ticker.iterkeys():
                items = cur.split("_")
                symbol_set.add(items[0]) # the first item is the base currency
        return list(symbol_set)


    def get_price(self, from_asset, to_asset, amount = 1.0):
        """
        Returns how much of from_asset you would have after exchanging it
        for the amount of from_asset based on the last price traded here.
        """
        symbols = self.get_symbols()
        from_asset = from_asset.upper()
        to_asset = to_asset.upper()
        if not from_asset in symbols:
            raise PriceSourceError("No such symbol %s" % from_asset)
        if not to_asset in symbols:
            raise PriceSourceError("No such symbol %s" % to_asset)
        
        if from_asset == to_asset:
            return amount

        inverse = False
        pol_symbol = to_asset + "_" + from_asset
        with self._lock:
            if not pol_symbol in self.pol_ticker.iterkeys():
                inverse = True
                pol_symbol = from_asset + "_" + to_asset
                if not pol_symbol in self.pol_ticker.iterkeys():
                    raise PriceSourceError("Missing market")

            # TODO: update pol_ticker in another thread periodically
            # or make a vectorized version of this function so we don't
            # have to keep updating the whole ticker if we are getting
            # many prices at once.
            self.pol_ticker = self.pol.returnTicker() #update it now
            price = float(self.pol_ticker[pol_symbol]["last"])
            if inverse:
                try:
                    price = 1.0/price
                except ZeroDivisionError:
                    pass
            return price * amount


class CryptoAssetCharts(PriceSource):
    """
    Scrapes from cryptoassetcharts.info to get crypto asset price info.
    """

    def __init__(self):
        self._req_url = "http://cryptoassetcharts.info/assets/info"
        self._asset_symbols = set()
        self._base_symbols = set()
        self._price_map = {}
        
        self._update_info()


    def _update_info(self):
        self._response = requests.get(self._req_url)
        doc = pq(self._response.content)
        tbl = doc("#tableAssets")
        self._price_map = {}
        for row in tbl.items('tr'):
            col = []
            for c in row.items('td'):
                c_text = c.text()
                if isinstance(c_text, unicode):
                    c_text = unicodedata.normalize('NFKD', c_text).encode('ascii','ignore')
                col.append(c_text)
            if len(col) < 7:
                continue
            asset_symbol = col[1]
            self._asset_symbols.add(asset_symbol)

            # extract last price from row
            price_str = col[4]
            price_str_comp = price_str.split(" ")
            base_symbol = price_str_comp[1]
            self._base_symbols.add(base_symbol)
            
            price_val = locale.atof(price_str_comp[0])
            self._price_map["_"+asset_symbol+"/"+base_symbol] = price_val
        

    def get_symbols(self):
        """
        List all asset symbols at the site.
        """

        # Prefix asset symbols with _ so they don't collide with other real symbol names.
        return ['_'+s for s in self._asset_symbols] + self.get_base_symbols()


    def get_base_symbols(self):
        """
        List base currencies that market prices are listed in.
        """
        return list(self._base_symbols)


    def get_price(self, from_asset, to_asset, amount = 1.0):
        """
        Returns the price as reflected in the price map
        """
        symbols = self.get_symbols()
        if not from_asset in symbols:
            raise PriceSourceError("No such symbol %s" % from_asset)
        if not to_asset in symbols:
            raise PriceSourceError("No such symbol %s" % to_asset)

        # nothing to do here
        if from_asset == to_asset:
            return amount

        inverse = False
        trade_pair_str = to_asset + '/' + from_asset
        if not trade_pair_str in self._price_map.iterkeys():
            inverse = True
            trade_pair_str = from_asset + '/' + to_asset
            if not trade_pair_str in self._price_map.iterkeys():
                raise PriceSourceError("Missing market")

        # TODO: update info in another thread periodically (see similar note in Poloniex)
        self._update_info()
        price = self._price_map[trade_pair_str]
        if inverse:
            try:
                price = 1.0/price
            except ZeroDivisionError:
                pass
        return price * amount


class Synthetic(PriceSource):
    """
    Contains mappings and conversions between symbols such as
    mNXT <-> NXT, XBT <-> BTC, etc.
    """

    def __init__(self):
        super(Synthetic, self).__init__()
        self._mapping = {
            "XBT/BTC": 1.0,
            "mNHZ/NHZ": 1000.0,
            "mNXT/NXT": 1000.0,
            "sat/BTC": 100000000,
            "_Coinomat1/Coinomat1": 1.0,
            "_MMNXT/MMNXT": 1.0
        }


    def get_symbols(self):
        symbols = set()
        for key in self._mapping.iterkeys():
            symbol = key.split("/")
            symbols.add(symbol[0])
            symbols.add(symbol[1])
        return list(symbols)


    def get_base_symbols(self):
        symbols = set()
        for key in self._mapping.iterkeys():
            symbol = key.split("/")
            symbols.add(symbol[1])
        return list(symbols)


    def get_price(self, from_asset, to_asset, amount = 1.0):
        """
        Uses the mapping to convert from_asset to to_asset.
        """
        symbols = self.get_symbols()
        if not from_asset in symbols:
            raise PriceSourceError("No such symbol %s" % from_asset)
        if not to_asset in symbols:
            raise PriceSourceError("No such symbol %s" % to_asset)

        # nothing to do here
        if from_asset == to_asset:
            return amount

        inverse = False
        trade_pair_str = to_asset + '/' + from_asset
        if not trade_pair_str in self._mapping.iterkeys():
            inverse = True
            trade_pair_str = from_asset + '/' + to_asset
            if not trade_pair_str in self._mapping.iterkeys():
                raise PriceSourceError("Missing market")

        price = self._mapping[trade_pair_str]
        if inverse:
            try:
                price = 1.0/price
            except ZeroDivisionError:
                pass
        return price * amount


class AllSources(PriceSource):
    """
    Uses info from all available sources
    """

    def __init__(self):
        # handles to price sources
        self._sources = {
            "bitfinex.com": Bitfinex(),
            "poloniex.com": Poloniex(),
            "cryptoassetcharts.info": CryptoAssetCharts(),
            "synthetic": Synthetic(),
        }


    def get_symbols(self):
        """
        Returns the set of all known symbols across all price sources.
        """
        symbols = set()
        for source_name, source in self._sources.iteritems():
            for symbol in source.get_symbols():
                symbols.add(symbol)
        return list(symbols)


    def get_base_symbols(self):
        """
        Returns the set of all known symbols across all price sources.
        """
        symbols = set()
        for source_name, source in self._sources.iteritems():
            for symbol in source.get_base_symbols():
                symbols.add(symbol)
        return list(symbols)


    def get_price(self, from_asset, to_asset, amount = 1.0):
        """
        Returns price detemrined as an average across all sources.
        """
        prices = []
        for source_name, source in self._sources.iteritems():
            try:
                price = source.get_price(from_asset, to_asset, amount)
                prices.append(price)
            except PriceSourceError:
                pass
        if len(prices) == 0:
            raise PriceSourceError("Couldn't determine price")
        return math.fsum(prices)/float(len(prices))
