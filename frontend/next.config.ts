import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  devIndicators: false,
  allowedDevOrigins: ["192.168.0.198"],
  experimental: {
    devtoolSegmentExplorer: false,
    browserDebugInfoInTerminal: false,
  },
};

export default nextConfig;
