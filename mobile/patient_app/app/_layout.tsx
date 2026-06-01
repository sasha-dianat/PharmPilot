/**
 * Root layout — wraps all screens with QueryClient and auth guard.
 */
import { useEffect } from 'react'
import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useAuthStore } from '../src/store/auth'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 2, staleTime: 30_000 },
  },
})

export default function RootLayout() {
  const { checkAuth, checkBiometricAvailability } = useAuthStore()

  useEffect(() => {
    checkAuth()
    checkBiometricAvailability()
  }, [])

  return (
    <QueryClientProvider client={queryClient}>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: '#0f172a' },
          animation: 'fade',
        }}
      >
        <Stack.Screen name="(auth)/login" options={{ headerShown: false }} />
        <Stack.Screen name="(tabs)"       options={{ headerShown: false }} />
      </Stack>
    </QueryClientProvider>
  )
}
