import { useEffect, useState } from "react";
import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export function useShipping() {
  const [shippingFee, setShippingFee] = useState(0);
  const [freeShippingThreshold, setFreeShippingThreshold] = useState(null);

  useEffect(() => {
    let cancel = false;
    axios
      .get(`${API}/settings`)
      .then((r) => {
        if (cancel) return;
        const s = r.data || {};
        setShippingFee(Number(s.shipping_fee) || 0);
        const t = s.free_shipping_threshold;
        setFreeShippingThreshold(t === null || t === undefined || t === "" ? null : Number(t));
      })
      .catch(() => {});
    return () => { cancel = true; };
  }, []);

  return { shippingFee, freeShippingThreshold };
}
