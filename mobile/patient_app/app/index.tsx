/**
 * Root redirect — checks auth state and routes accordingly.
 */
import { useEffect } from 'react'
import { View, ActivityIndicator } from 'react-native'
import { router } from 'expo-router'
import { useAuthStore } from '../src/store/auth'

export default function Index() {
  const { isAuthenticated, loading } = useAuthStore()

  useEffect(() => {
    if (!loading) {
      if (isAuthenticated) {
        router.replace('/(tabs)/prescriptions')
      } else {
        router.replace('/(auth)/login')
      }
    }
  }, [isAuthenticated, loading])

  return (
    <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: '#0f172a' }}>
      <ActivityIndicator size="large" color="#3b82f6" />
    </View>
  )
}
