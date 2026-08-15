/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Lock image optimization to known hosts (SSRF hardening — see spec Security).
  images: {
    remotePatterns: [],
  },
};

export default nextConfig;
