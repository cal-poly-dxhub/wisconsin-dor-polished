'use client';

import React, { createContext, useContext, useEffect, useState } from 'react';
import { CognitoUserSession } from 'amazon-cognito-identity-js';
import {
  signIn as cognitoSignIn,
  signUp as cognitoSignUp,
  signOut as cognitoSignOut,
  confirmSignUp as cognitoConfirmSignUp,
  getCurrentSession,
  resendConfirmationCode as cognitoResendConfirmationCode,
  SignInParams,
  SignUpParams,
  ConfirmSignUpParams,
} from '@/lib/auth';

interface AuthContextType {
  session: CognitoUserSession | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  signIn: (params: SignInParams) => Promise<void>;
  signUp: (params: SignUpParams) => Promise<{ userConfirmed: boolean }>;
  confirmSignUp: (params: ConfirmSignUpParams) => Promise<void>;
  signOut: () => Promise<void>;
  resendConfirmationCode: (email: string) => Promise<void>;
  refreshSession: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [session, setSession] = useState<CognitoUserSession | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refreshSession = async () => {
    try {
      const currentSession = await getCurrentSession();
      setSession(currentSession);
    } catch (error) {
      console.error('Failed to refresh session:', error);
      setSession(null);
    }
  };

  useEffect(() => {
    const initAuth = async () => {
      try {
        await refreshSession();
      } finally {
        setIsLoading(false);
      }
    };

    initAuth();
  }, []);

  const signIn = async (params: SignInParams) => {
    const session = await cognitoSignIn(params);
    setSession(session);
  };

  const signUp = async (
    params: SignUpParams
  ): Promise<{ userConfirmed: boolean }> => {
    const result = await cognitoSignUp(params);
    return { userConfirmed: result.userConfirmed };
  };

  const confirmSignUp = async (params: ConfirmSignUpParams) => {
    await cognitoConfirmSignUp(params);
  };

  const signOut = async () => {
    await cognitoSignOut();
    setSession(null);
  };

  const resendConfirmationCode = async (email: string) => {
    await cognitoResendConfirmationCode(email);
  };

  return (
    <AuthContext.Provider
      value={{
        session,
        isLoading,
        isAuthenticated: !!session,
        signIn,
        signUp,
        confirmSignUp,
        signOut,
        resendConfirmationCode,
        refreshSession,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
